"""Free internet search for the chatbot (no API key, no Docker).

Providers (order configurable):  searxng (optional, self-hosted)  ->  duckduckgo (`ddgs` package)  ->  wikipedia.

Design rules
- search() never raises. Failure => empty results + reason in `Outcome.error`.
- PRIVACY: query is sanitized AND passes a privacy gate (patient/identifier terms, emails, long numbers,
  company-specific block terms). A blocked query is never sent anywhere.
- IP SAFETY (many users, one server IP): result cache + single-flight (same question = 1 outbound request),
  per-user and server-wide token buckets, max concurrent outbound calls, and a circuit breaker per provider
  (after a failure/rate-limit the provider is left alone for a growing cool-down instead of being hammered).
- Results are short (title, url, snippet) so they fit the 4096-token model context. Only http/https urls.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import string
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote, urlparse

import httpx

from app.core.ratelimit import TokenBucketLimiter

log = logging.getLogger("websearch")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_LONGNUM = re.compile(r"\b\d[\d\-\s]{7,}\d\b")

# Fail-closed: in lafzon wala sawal internet par nahi jata (model apne ilm se jawab deta hai).
DEFAULT_PRIVACY_TERMS = (
    "patient,patients,mrn,cnic,hospital number,bed number,bed no,ward no,admitted,discharge summary,"
    "mareez,mareeza,مریض,مریضہ"
)


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")


@dataclass
class Outcome:
    results: list[WebResult] = field(default_factory=list)
    provider: str = ""
    error: str = ""
    query: str = ""            # the sanitized query that was actually sent
    blocked: bool = False      # privacy gate ne roka
    limited: bool = False      # rate limit
    retry_after: float = 0.0


def sanitize_query(q: str, max_chars: int = 200) -> str:
    """Remove things that must not leave the company: emails, urls, long digit strings (phones, accounts)."""
    q = _URL.sub(" ", q or "")
    q = _EMAIL.sub(" ", q)
    q = _LONGNUM.sub(" ", q)
    q = _WS.sub(" ", q).strip()
    return q[:max_chars].strip()


def privacy_block_reason(raw: str, terms: str = DEFAULT_PRIVACY_TERMS) -> str:
    """'' = theek hai; warna wajah. Raw (sanitize se pehle) text check hota hai."""
    text = raw or ""
    if _EMAIL.search(text):
        return "contains an email address"
    if _LONGNUM.search(text):
        return "contains a long number (phone / ID / record number)"
    low = text.lower()
    for term in (t.strip().lower() for t in terms.split(",")):
        if not term:
            continue
        if term.isascii():
            if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", low):
                return "mentions a private/internal term"
        elif term in low:
            return "mentions a private/internal term"
    return ""


_STOP = frozenset("""what who whom whose when where which how why the and for are was were with that this from have has does did can could
would should about there their into your you tell give please check online search current latest whi wat
kia kya hai hain ka ki ke ko se mein par aur kaun kon kitna kitni kitne batao bata mujhe mujhy yeh wo woh aaj abhi""".split())


def keywords(q: str) -> list[str]:
    return [t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3 and t not in _STOP]


def is_relevant(keys: list[str], r: "WebResult") -> bool:
    """Kam az kam ek keyword title/snippet mein (typo ke liye 5+ harf wale ke pehle 4 harf)."""
    if not keys:
        return True
    hay = f"{r.title} {r.snippet}".lower()
    squashed = re.sub(r"[\W_]+", "", hay)                  # "EU MDR" / "EU-MDR" vs "eumdr"
    return any(k in hay or k in squashed or (len(k) >= 5 and k[:4] in hay) for k in keys)


def _norm_key(q: str) -> str:
    table = str.maketrans("", "", string.punctuation)
    return _WS.sub(" ", q.lower().translate(table)).strip()


def _clean(text: str, limit: int) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ok_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and bool(u.hostname)
    except ValueError:
        return False


def _ddg_search(query: str, n: int, timeout: float = 8.0, proxy: str = "") -> list[dict]:
    from ddgs import DDGS  # lazy: only needed when internet search is enabled

    kwargs: dict = {"timeout": max(1, int(timeout))}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        client = DDGS(**kwargs)
    except TypeError:                      # purana/naya ddgs version: in kwargs ko support nahi karta
        client = DDGS()
    return list(client.text(query, max_results=n))


class WebSearch:
    def __init__(
        self,
        *,
        max_results: int = 4,
        timeout: float = 8.0,
        cache_ttl: float = 900.0,
        snippet_chars: int = 300,
        blocked_domains: tuple[str, ...] = (),
        wikipedia_lang: str = "en",
        providers: tuple[str, ...] = ("duckduckgo", "wikipedia"),
        searxng_url: str = "",
        proxy: str = "",
        contact: str = "",
        privacy_terms: str = DEFAULT_PRIVACY_TERMS,
        per_user_per_minute: int = 5,
        global_per_minute: int = 20,
        max_concurrent: int = 2,
        cooldown_base: float = 60.0,
        cooldown_max: float = 1800.0,
        ddg: Callable[..., list[dict]] | None = None,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_results = max_results
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.snippet_chars = snippet_chars
        self.blocked = tuple(d.lower() for d in blocked_domains)
        self.lang = wikipedia_lang
        self.searxng_url = searxng_url.rstrip("/")
        self.proxy = proxy
        # Wikimedia policy: User-Agent mein contact hona chahiye, warna 403 aa sakta hai
        self.user_agent = "CompanyAI/1.0 (internal assistant" + (f"; {contact}" if contact else "") + ")"
        self.privacy_terms = privacy_terms
        self.cooldown_base = cooldown_base
        self.cooldown_max = cooldown_max
        self._ddg = ddg or _ddg_search
        self._client = client
        self._clock = clock
        self._providers = [p for p in providers if p != "searxng" or self.searxng_url]
        self._user_limiter = TokenBucketLimiter(per_user_per_minute, max(1, per_user_per_minute), clock=clock) if per_user_per_minute > 0 else None
        self._global_limiter = TokenBucketLimiter(global_per_minute, max(1, min(global_per_minute, 10)), clock=clock) if global_per_minute > 0 else None
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_concurrent), thread_name_prefix="ddg")
        self._cache: dict[str, tuple[float, Outcome]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------- providers ----------
    async def _from_ddg(self, q: str) -> list[WebResult]:
        loop = asyncio.get_running_loop()
        call = lambda: self._ddg(q, self.max_results * 2, self.timeout, self.proxy)  # noqa: E731
        rows = await asyncio.wait_for(loop.run_in_executor(self._pool, call), self.timeout + 2)
        out: list[WebResult] = []
        for r in rows or []:
            url = str(r.get("href") or r.get("url") or "")
            if _ok_url(url):
                out.append(WebResult(_clean(str(r.get("title", "")), 120), url, _clean(str(r.get("body", "")), self.snippet_chars)))
        return out

    async def _http_json(self, url: str, params: dict) -> dict:
        client = self._client or httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": self.user_agent})
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        finally:
            if self._client is None:
                await client.aclose()

    async def _from_searxng(self, q: str) -> list[WebResult]:
        data = await self._http_json(f"{self.searxng_url}/search", {"q": q, "format": "json", "safesearch": 1})
        out = []
        for r in data.get("results", []):
            url = str(r.get("url", ""))
            if _ok_url(url):
                out.append(WebResult(_clean(str(r.get("title", "")), 120), url, _clean(str(r.get("content", "")), self.snippet_chars)))
        return out

    async def _from_wikipedia(self, q: str) -> list[WebResult]:
        params = {
            "action": "query", "format": "json", "utf8": 1, "generator": "search", "gsrsearch": " ".join(keywords(q)) or q,
            "gsrlimit": self.max_results, "prop": "extracts", "exintro": 1, "explaintext": 1, "exsentences": 3,
        }
        data = await self._http_json(f"https://{self.lang}.wikipedia.org/w/api.php", params)
        pages = list((data.get("query", {}).get("pages", {})).values())
        pages.sort(key=lambda p: p.get("index", 99))
        out = []
        for p in pages:
            title = str(p.get("title", ""))
            extract = _clean(str(p.get("extract", "")), self.snippet_chars)
            if title and extract:
                out.append(WebResult(title, f"https://{self.lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}", extract))
        return out

    # ---------- circuit breaker ----------
    def _is_open(self, name: str) -> bool:
        return self._clock() < self._open_until.get(name, 0.0)

    def _record_failure(self, name: str) -> None:
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        self._open_until[name] = self._clock() + min(self.cooldown_base * 2 ** (n - 1), self.cooldown_max)

    def _record_success(self, name: str) -> None:
        self._fails.pop(name, None)
        self._open_until.pop(name, None)

    def provider_status(self) -> dict:
        """Admin/debug ke liye: kaun sa provider cool-down mein hai."""
        now = self._clock()
        return {p: {"fails": self._fails.get(p, 0), "cooldown_s": max(0, round(self._open_until.get(p, 0) - now))} for p in self._providers}

    # ---------- core ----------
    async def _fetch(self, q: str) -> Outcome:
        fns = {"searxng": self._from_searxng, "duckduckgo": self._from_ddg, "wikipedia": self._from_wikipedia}
        errors: list[str] = []
        keys = keywords(q)
        async with self._sem:
            for name in self._providers:
                if self._is_open(name):
                    errors.append(f"{name}: cooling down")
                    continue
                try:
                    results = await fns[name](q)
                except Exception as exc:  # network, rate limit, missing package, timeout
                    errors.append(f"{name}: {type(exc).__name__}")
                    self._record_failure(name)
                    log.warning("web search via %s failed: %s (cool-down %ss)", name, type(exc).__name__,
                                round(self._open_until[name] - self._clock()))
                    continue
                self._record_success(name)
                results = [r for r in results if not any(r.domain.endswith(b) for b in self.blocked)]
                # DDG/SearXNG ranking par bharosa; sirf Wikipedia fallback (jo bekar page de sakta hai) filter hota hai
                kept = [r for r in results if name != "wikipedia" or is_relevant(keys, r)]
                if results and not kept:
                    errors.append(f"{name}: irrelevant results dropped")
                results = kept[: self.max_results]
                if results:
                    return Outcome(results=results, provider=name, query=q, error="; ".join(errors))
                errors.append(f"{name}: no results")
        return Outcome(query=q, error="; ".join(errors) or "no results")

    async def search(self, query: str, user: str = "") -> Outcome:
        reason = privacy_block_reason(query, self.privacy_terms)
        if reason:
            return Outcome(error=f"blocked: {reason}", blocked=True)
        q = sanitize_query(query)
        if len(q) < 3:
            return Outcome(query=q, error="query too short after cleaning")

        key = _norm_key(q)
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < self.cache_ttl:
            return hit[1]

        pending = self._inflight.get(key)
        if pending is not None:                       # same sawal pehle se chal raha hai: usi ka intezar
            return await asyncio.shield(pending)

        for limiter, who, code in ((self._user_limiter, user or "-", "user"), (self._global_limiter, "all", "server")):
            if limiter is None:
                continue
            ok, retry = limiter.allow(who)
            if not ok:
                return Outcome(query=q, error=f"rate limited ({code})", limited=True, retry_after=retry)

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        outcome = Outcome(query=q, error="cancelled")
        try:
            outcome = await self._fetch(q)
            if outcome.results:
                self._cache[key] = (self._clock(), outcome)
                if len(self._cache) > 256:
                    self._cache.pop(next(iter(self._cache)))
            return outcome
        finally:
            self._inflight.pop(key, None)
            if not fut.done():
                fut.set_result(outcome)


# ---------- prompt + source helpers (pure) ----------
def build_web_context(results: list[WebResult]) -> str:
    return "\n".join(f"[{i}] {r.title} ({r.domain})\n{r.snippet}" for i, r in enumerate(results, 1))


def to_sources(results: list[WebResult]) -> list[dict]:
    """Same shape as document sources, plus kind/url/title (the frontend shows them as website cards)."""
    return [
        {"ref": i, "kind": "web", "doc_id": r.domain, "title": r.title, "url": r.url, "preview": r.snippet, "chunk_index": 0, "score": None}
        for i, r in enumerate(results, 1)
    ]


if __name__ == "__main__":  # python -m app.services.websearch "your question"
    import json
    import sys

    async def _main() -> None:
        out = await WebSearch().search(" ".join(sys.argv[1:]) or "what is stainless steel 420")
        print(json.dumps({"provider": out.provider, "error": out.error, "query": out.query, "results": to_sources(out.results)}, indent=2, ensure_ascii=False))

    asyncio.run(_main())
