import hashlib
import json
import logging
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.api.deps import get_client
from app.api.errors import raise_mapped
from app.api.streaming import record_usage, stream_chat
from app.core.config import settings, SYSTEM_PROMPT
from app.core.fairness import ClientBusy
from app.core.metrics import CACHE_REQUESTS
from app.core.security import ClientInfo
from app.services import container
from app.services.chat_service import chat_service
from app.services.messages import build_messages

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    """Ya to `message` (single turn) ya `messages` (history, aakhri role=user)."""

    message: str | None = Field(default=None, min_length=1, max_length=12000)

    messages: list[ChatMessage] | None = Field(
        default=None, min_length=1, max_length=settings.CHAT_MAX_MESSAGES
    )

    temperature: float = Field(default=0.2, ge=0, le=2)

    # llama-server -c 4096 hai, prompt bhi usi context mein aata hai.
    max_tokens: int = Field(default=300, ge=1, le=2048)

    @model_validator(mode="after")
    def _check_input(self):
        if (self.message is None) == (self.messages is None):
            raise ValueError("provide exactly one of 'message' or 'messages'")
        if self.messages is not None and self.messages[-1].role != "user":
            raise ValueError("the last message must have role 'user'")
        return self


def _prepare_messages(request: ChatRequest) -> list[dict]:
    history = (
        [m.model_dump() for m in request.messages]
        if request.messages is not None else None
    )
    return build_messages(
        request.message,
        history,
        SYSTEM_PROMPT,
        settings.CHAT_HISTORY_MAX_CHARS,
    )


def _cache_key(request: ChatRequest, prepared: list[dict]) -> str:
    raw = json.dumps(
        {
            "model": settings.LLAMA_MODEL,
            "messages": prepared,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "chat:" + hashlib.sha256(raw.encode()).hexdigest()


def _parse_completion(result: dict) -> dict:
    choices = result.get("choices", [])

    if not choices:
        raise HTTPException(502, "Inference provider returned no choices")

    content = choices[0].get("message", {}).get("content")

    if content is None:
        raise HTTPException(502, "Inference provider returned invalid content")

    return {"response": content, "usage": result.get("usage")}


@router.post("/completions")
async def chat_completion(
    request: ChatRequest,
    response: Response,
    client: ClientInfo = Depends(get_client),
):
    started = perf_counter()
    prepared = _prepare_messages(request)
    cache = container.response_cache
    cacheable = cache is not None and request.temperature <= settings.CACHE_MAX_TEMPERATURE

    async def compute():
        result = await chat_service.generate(
            message=None,
            messages=prepared,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return _parse_completion(result)

    limiter = container.client_limiter
    try:
        limiter.acquire(client.name)
    except ClientBusy as exc:
        raise_mapped(exc)

    try:
        try:
            if cacheable:
                payload, source = await cache.get_or_compute(
                    _cache_key(request, prepared), compute
                )
            else:
                payload, source = await compute(), "bypass"

        except HTTPException:
            await record_usage(client, "completions", "error", started)
            raise

        except Exception as exc:
            await record_usage(client, "completions", "error", started)
            raise_mapped(exc)

    finally:
        limiter.release(client.name)

    CACHE_REQUESTS.labels(result=source).inc()
    response.headers["X-Cache"] = source.upper()

    await record_usage(
        client, "completions", "success", started,
        cache=source, usage=payload.get("usage"),
    )

    return {
        "status": "success",
        "model": settings.LLAMA_MODEL,
        "response": payload["response"],
        "usage": payload.get("usage"),
    }


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    client: ClientInfo = Depends(get_client),
):
    return await stream_chat(
        http_request,
        client,
        _prepare_messages(request),
        request.temperature,
        request.max_tokens,
        endpoint="stream",
    )
