import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.api.deps import ensure_collection_access, get_client
from app.api.errors import raise_mapped
from app.api.streaming import stream_chat
from app.core.config import settings, SYSTEM_PROMPT
from app.core.security import ClientInfo
from app.database.conversations import DEFAULT_TITLE, title_from
from app.services import container
from app.services.agent import with_clock
from app.services.messages import build_messages
from app.services.router import (
    about_bot_signal, chitchat_or_task, company_fact_signal, contextual_text, docs_min_score, expand_terms, is_reformat_request,
    regulatory_signal, roman_urdu_signal, topic_overlap,
)
from app.services.regsearch import lock_collections, search_regulatory
from app.services.tools import run_tools

logger = logging.getLogger("company_ai.conversations")
router = APIRouter(prefix="/conversations", tags=["Conversations"])

_ID = r"^[a-f0-9]{32}$"


class CreateConversation(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class RenameConversation(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationChat(BaseModel):
    """Naya `message` bhejo, ya `regenerate=true` (aakhri assistant jawab dobara)."""

    message: str | None = Field(default=None, min_length=1, max_length=12000)
    regenerate: bool = False
    # Set ho to company documents (us collection) se jawab + sources.
    # "*" = Auto: user ki sab collections mein dhoondo; kuch na mile to tools / internet / general
    collection: str | None = Field(default=None, max_length=100, pattern=r"^(\*|[\w.\-:]+)$")
    # Internet: auto (router faisla kare) | on (hamesha) | off (kabhi nahi). collection set ho to internet band.
    web_search: Literal["auto", "on", "off"] = "auto"
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(default=300, ge=1, le=2048)

    @model_validator(mode="after")
    def _check(self):
        if (self.message is None) == (not self.regenerate):
            raise ValueError("provide either 'message' or 'regenerate': true")
        return self


async def _owned(conversation_id: str, client: ClientInfo) -> dict:
    conv = await container.conversations_repo.get(conversation_id, client.name)
    if conv is None:
        raise HTTPException(404, "Conversation not found")      # doosre ka ho ya na ho: same jawab
    return conv


@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    client: ClientInfo = Depends(get_client),
):
    return {"conversations": await container.conversations_repo.list_conversations(
        client.name, limit, offset)}


@router.post("", status_code=201)
async def create_conversation(
    body: CreateConversation | None = None,
    client: ClientInfo = Depends(get_client),
):
    title = body.title if body and body.title else DEFAULT_TITLE
    return await container.conversations_repo.create(client.name, title)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str = Path(pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    conv = await _owned(conversation_id, client)
    messages = await container.conversations_repo.list_messages(conversation_id, limit=500)
    return {**conv, "messages": messages}


@router.patch("/{conversation_id}")
async def rename_conversation(
    body: RenameConversation,
    conversation_id: str = Path(pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    await _owned(conversation_id, client)
    await container.conversations_repo.rename(conversation_id, client.name, body.title)
    return {"id": conversation_id, "title": body.title}


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str = Path(pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    await _owned(conversation_id, client)
    await container.conversations_repo.delete(conversation_id, client.name)
    return {"status": "deleted"}


@router.post("/{conversation_id}/chat")
async def conversation_chat(
    body: ConversationChat,
    http_request: Request,
    conversation_id: str = Path(pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    """
    ChatGPT-style: server history khud load karta hai. Stream (SSE) hota hai, aur
    jawab complete/stopped hone par save. Fail hone par (429/502...) kuch save nahi hota.
    """
    repo = container.conversations_repo
    await _owned(conversation_id, client)

    stored = [m for m in await repo.list_messages(conversation_id, limit=settings.CHAT_MAX_MESSAGES) if m["content"]]
    history = [{"role": m["role"], "content": m["content"]} for m in stored]

    if body.regenerate:
        if history and history[-1]["role"] == "assistant":
            history.pop()
        if not history or history[-1]["role"] != "user":
            raise HTTPException(400, "Nothing to regenerate")
    else:
        history.append({"role": "user", "content": body.message})

    saved_sources = None       # RAG ya web sources (DB mein assistant message ke saath)
    preamble = None

    question = history[-1]["content"]
    previous = next(
        (m["content"] for m in reversed(history[:-1]) if m["role"] == "user"), ""
    )
    # "roman urdu mai batao" / "short karo": pichle jawab par hukm. Naya sawal nahi: documents/internet nahi, sirf dobara likhna.
    reformat = is_reformat_request(question) and any(m["role"] == "assistant" for m in history[:-1])
    if reformat:
        # Translation/short/simple: pichle jawab ke sources saath chalte hain, taake [n] aur origin label sahi rahein
        saved_sources = next((m.get("sources") for m in reversed(stored) if m["role"] == "assistant"), None)
        if saved_sources:
            preamble = "event: sources\ndata: " + json.dumps({"sources": saved_sources}) + "\n\n"
        last_answer = next((m["content"] for m in reversed(history[:-1]) if m["role"] == "assistant"), "")
        # Sirf pichla jawab + hukm: warna model purane jawabon ka style/label ("General knowledge:") copy kar deta tha
        history[:] = [
            {"role": "assistant", "content": last_answer},
            {"role": "user", "content": (
                f"{question}\n\n(Rewrite your previous answer exactly as requested above, as plain text. Use only the facts, "
                "numbers and [n] citations already in it. Do not add new facts, labels, greetings or notes, and do not answer a new question.)")},
        ]
    use_docs = bool(body.collection) and not reformat
    hits: list[dict] = []

    if body.collection == "*" and not reformat:
        # Auto: documents pehle. Tool (calculator/unit/time) wale sawal par documents ko chhedte hi nahi.
        use_docs = False
        rag = container.rag_service
        min_score = None if body.web_search == "on" else docs_min_score(question, settings.RAG_AUTO_MIN_SCORE)
        # Salam/shukriya, "tum kon ho", likhne ka kaam: documents mein dhoondna hi nahi (bekaar sources aate the)
        skip_docs = chitchat_or_task(question) == "greeting" or about_bot_signal(question)
        if skip_docs:
            logger.info("auto mode: skipped document search (greeting / about the assistant)")
        if min_score is not None and not skip_docs and not run_tools(question):
            retrieval_query = expand_terms(contextual_text(question, previous))
            try:
                names = [n for n in (await rag.stats())["collections"] if client.can_access(n)]
                found, hits = [], []
                if regulatory_signal(question) or regulatory_signal(retrieval_query):
                    # FDA / regulation / qanoon: pehle shared reg__* (openFDA, eCFR, EU/UK/AU). Sawal jis region ka ho sirf usi
                    # ki reg__* (EU poochha to reg__eu; US FDA kabhi mix nahi), fallback bhi us region lock ke andar.
                    res = await search_regulatory(rag, names, question, retrieval_query, min_score)
                    found, hits = res["found"], res["hits"]
                else:
                    scoped_names = lock_collections(names, question, retrieval_query)   # region likha ho to bhi lock
                    found = await rag.search_many(retrieval_query, scoped_names)
                    hits = [h for h in found if h["score"] >= min_score]
                top = found[0]["score"] if found else None
                if hits and company_fact_signal(question) and not chitchat_or_task(question):
                    # "Do we have FDA approval / our MOQ": general reference documents company ka claim sabit nahi karte
                    general = {x.strip() for x in settings.RAG_GENERAL_DOC_IDS.split(",") if x.strip()}
                    hits = [h for h in hits if h["doc_id"] not in general]
                # Sawal ke khaas lafz se bilkul na milne wale chunks nikaal do (sirf score par bharosa nahi)
                hits = [h for h in hits if topic_overlap(retrieval_query, h["text"])]
                logger.info("auto mode: collections=%d top_score=%s used=%d", len(names), top, len(hits))
            except Exception:
                logger.exception("auto mode: document search failed; falling back to tools/internet")
                hits = []
            use_docs = bool(hits)

    if use_docs:
        rag = container.rag_service
        if body.collection != "*":
            ensure_collection_access(client, body.collection)
            # Sirf asli follow-up ("aur digital goods ka?") par pichla sawal retrieval query mein
            retrieval_query = expand_terms(contextual_text(question, previous))
            try:
                hits = rag.relevant(await rag.search(retrieval_query, body.collection))
            except Exception as exc:
                raise_mapped(exc)

        # Model ko context+sawal; DB mein sirf asli sawal save hota hai
        prompt, saved_sources = rag.build_prompt(expand_terms(question), hits)
        if roman_urdu_signal(question):                 # chhota model English pe aa jata hai: aakhri line yaad rehti hai
            prompt += "\n\n(The question is in Roman Urdu: write the answer in Roman Urdu, using English letters only.)"
        history[-1] = {"role": "user", "content": prompt}
        preamble = "event: sources\ndata: " + json.dumps({"sources": saved_sources}) + "\n\n"

    elif container.agent is not None and not reformat:
        # Tools (calculator/unit/time) ya internet search; search GPU slot se pehle hoti hai
        prep = await container.agent.prepare(
            user=client.name, question=question, previous_user=previous, web_mode=body.web_search,
        )
        if prep.prompt:
            hint = ("\n\n(The question is in Roman Urdu: write the answer in Roman Urdu, using English letters only.)"
                    if roman_urdu_signal(question) else "")
            history[-1] = {"role": "user", "content": prep.prompt + hint}
        saved_sources, preamble = prep.sources, prep.preamble

    system_prompt = SYSTEM_PROMPT
    if container.agent is not None:
        system_prompt = with_clock(system_prompt, container.agent.timezone)

    prepared = build_messages(
        None, history, system_prompt, settings.CHAT_HISTORY_MAX_CHARS
    )

    async def on_start():
        if body.regenerate:
            await repo.delete_last_assistant(conversation_id)
        else:
            await repo.add_message(conversation_id, "user", body.message)
            await repo.set_title_if_default(conversation_id, title_from(body.message))

    async def on_finish(text: str, outcome: str):
        if text.strip():
            status = {"success": "complete", "cancelled": "stopped"}.get(outcome, "error")
            await repo.add_message(
                conversation_id, "assistant", text, status, sources=saved_sources
            )

    temperature = min(body.temperature, settings.RAG_ANSWER_MAX_TEMPERATURE) if use_docs else body.temperature

    return await stream_chat(
        http_request,
        client,
        prepared,
        temperature,
        body.max_tokens,
        endpoint="conversation_chat",
        on_start=on_start,
        on_finish=on_finish,
        preamble=preamble,
    )
