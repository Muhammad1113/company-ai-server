"""Orchestrator: sawal -> (tools | web | direct) -> model ke liye prompt + UI events.

Sirf conversation chat (non-RAG) ke liye. Search GPU slot lene se PEHLE hoti hai (GPU search ke dauran free rehta hai).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from app.services.router import (
    chitchat_or_task, company_fact_signal, lookup_signal,
    Plan, build_classifier_prompt, build_company_fact_prompt, build_fallback_prompt, build_no_web_prompt, build_tool_prompt, build_unverified_prompt, build_web_prompt,
    parse_label, plan_route,
)
from app.services.tools import format_now, now_in, run_tools
from app.services.websearch import WebSearch, build_web_context, to_sources

logger = logging.getLogger("company_ai.agent")


@dataclass
class Prepared:
    route: str = "direct"
    prompt: str | None = None            # None => aakhri user message jaisa hai waisa
    sources: list[dict] | None = None    # DB mein assistant message ke saath save hote hain
    events: list[str] = field(default_factory=list)   # SSE events (generation se pehle)

    @property
    def preamble(self) -> str | None:
        return "".join(self.events) or None


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def with_clock(system_prompt: str, tz: str) -> str:
    """System prompt mein aaj ki date/time (Qwen ko khud maloom nahi hoti)."""
    clock = (
        f"Current local date and time: {format_now(now_in(tz))}. "
        "Your training data ends earlier: for recent events say you may be out of date instead of guessing."
    )
    return f"{system_prompt}\n\n{clock}" if system_prompt else clock


def make_llm_classifier(generate, timeout: float = 10.0):
    """generate(prompt, temperature, max_tokens) -> OpenAI-shape dict (chat_service.generate: queue/GPU slot se guzarta hai).
    Hamesha 'general' | 'lookup' | '' (error/timeout/samajh nahi aaya) wapas karta hai; kabhi raise nahi karta."""

    async def classify(question: str) -> str:
        try:
            res = await asyncio.wait_for(generate(build_classifier_prompt(question), 0.0, 8), timeout)
            return parse_label(res["choices"][0]["message"]["content"])
        except Exception as exc:  # queue full, timeout, model down...
            logger.warning("classifier failed: %s", type(exc).__name__)
            return ""

    return classify


class Agent:
    def __init__(self, web: WebSearch | None, *, timezone: str = "Asia/Karachi", policy: str = "auto",
                 classify=None) -> None:
        self.web = web
        self.timezone = timezone
        self.policy = policy
        self.classify = classify                 # async (question) -> 'general'|'lookup'|''  (None => shak par internet)
        self._labels: dict[str, str] = {}        # chhota cache: wahi sawal dobara => dobara GPU nahi

    async def _label(self, question: str) -> str:
        if self.classify is None:
            return ""
        key = " ".join(question.lower().split())
        if key in self._labels:
            return self._labels[key]
        label = await self.classify(question)
        if label:                                 # nakam/ghair-wazeh faisla cache nahi hota
            if len(self._labels) > 512:
                self._labels.pop(next(iter(self._labels)))
            self._labels[key] = label
        return label

    async def prepare(
        self,
        *,
        user: str,
        question: str,
        previous_user: str = "",
        collection: str | None = None,
        web_mode: str = "auto",
    ) -> Prepared:
        now = now_in(self.timezone)
        tools = [] if collection else run_tools(question, now)
        if not collection and not tools and company_fact_signal(question) and not chitchat_or_task(question):
            # Documents mein kuch nahi mila (auto mode pehle documents dekhta hai): company ke facts ka guess nahi
            logger.info("route=direct reason=company fact, no document found")
            return Prepared("direct", build_company_fact_prompt(question), None,
                            [_event("status", {"route": "direct", "company_fact": "not_found"})])
        plan: Plan = plan_route(
            question, previous_user,
            collection=collection, web_mode=web_mode, web_available=self.web is not None, tools=tools,
            policy=self.policy,
        )
        if plan.route == "ask":
            label = await self._label(plan.web_query or question)
            if label == "general":
                plan = Plan("direct", "classifier: general knowledge")
            else:                                # lookup, ya faisla na ho saka => internet (ghalat jawab se behtar)
                plan = Plan("web", f"classifier: {label or 'unsure'}", web_query=plan.web_query)
        logger.info("route=%s reason=%s", plan.route, plan.reason)       # sawal ka text log nahi hota

        if (plan.route == "direct" and plan.reason.startswith("web search ")
                and lookup_signal(question) and not chitchat_or_task(question)):
            # Internet band (user ne ya server par) aur sawal specs/standards/HS code ka: model number guess na kare
            why = "internet search is turned off" if "user" in plan.reason else "internet search is not available"
            return Prepared("direct", build_unverified_prompt(question, format_now(now), why), None,
                            [_event("status", {"route": "direct", "web": "off"})])

        if plan.route == "tool":
            names = [t.name for t in plan.tools]
            sources = [
                {"ref": i, "kind": "tool", "doc_id": t.name, "title": t.name, "preview": t.summary,
                 "chunk_index": 0, "score": None}
                for i, t in enumerate(plan.tools, 1)
            ]
            return Prepared("tool", build_tool_prompt(question, plan.tools), sources, [
                _event("status", {"route": "tool", "tools": names}),
                _event("sources", {"sources": sources}),
            ])

        if plan.route != "web":
            return Prepared(plan.route)

        assert self.web is not None
        outcome = await self.web.search(plan.web_query, user=user)
        when = format_now(now)

        if outcome.results:
            sources = to_sources(outcome.results)
            prompt = build_web_prompt(question, build_web_context(outcome.results), when)
            return Prepared("web", prompt, sources, [
                _event("status", {"route": "web", "query": outcome.query, "provider": outcome.provider}),
                _event("sources", {"sources": sources}),
            ])

        # Search nahi ho saki: model ko batao ke current cheez guess na kare (hallucination roko)
        retry = 0
        if outcome.blocked:
            state, why = "blocked", "the question contains private information, so it was not sent out"
        elif outcome.limited:
            retry = int(outcome.retry_after) + 1
            state, why = "busy", f"search limit reached, try again in about {retry} seconds"
        elif "cooling down" in outcome.error:
            state, why = "unavailable", "the search service is temporarily rate-limited, try again in a few minutes"
        else:
            state, why = "unavailable", "the search returned no usable result"
        logger.warning("web search not used (%s): %s", state, outcome.error)      # asal wajah yahan: limit/cooldown/no results
        # Current/user-ne-maanga: guess nahi. Specs/standards/dose: general samjhao, number nahi. Baaqi: general knowledge se
        # poora jawab (jawab ke pehle line mein wajah). Kabhi khali jawab nahi.
        if plan.fresh:
            prompt = build_no_web_prompt(question, when, why)
        elif lookup_signal(question):
            prompt = build_unverified_prompt(question, when, why)
        else:
            prompt = build_fallback_prompt(question, when, why)
        return Prepared("direct", prompt, None,
                        [_event("status", {"route": "direct", "web": state, "retry_after": retry})])
