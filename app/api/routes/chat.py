import asyncio
import hashlib
import json
import logging
from time import perf_counter

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.api.deps import get_client
from app.api.errors import map_error, raise_mapped
from app.core.concurrency import inference_controller
from app.core.config import settings
from app.core.metrics import CACHE_REQUESTS
from app.core.request_context import request_id_var
from app.core.security import ClientInfo
from app.services import container
from app.services.chat_service import chat_service

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

_background: set = set()


def _fire(coro):
    """Fire-and-forget (cancel ke dauran bhi record na khoye)."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


class ChatRequest(BaseModel):

    message: str = Field(min_length=1, max_length=12000)

    temperature: float = Field(default=0.2, ge=0, le=2)

    # llama-server -c 4096 hai, prompt bhi usi context mein aata hai.
    max_tokens: int = Field(default=300, ge=1, le=2048)


def _cache_key(request: ChatRequest) -> str:
    raw = json.dumps(
        {
            "model": settings.LLAMA_MODEL,
            "message": request.message,
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


async def _record(client, endpoint, status, started, cache=None, usage=None):
    usage = usage or {}
    await container.usage_repo.record(
        client=client.name,
        endpoint=endpoint,
        status=status,
        request_id=request_id_var.get(),
        cache=cache,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=(perf_counter() - started) * 1000,
    )


@router.post("/completions")
async def chat_completion(
    request: ChatRequest,
    response: Response,
    client: ClientInfo = Depends(get_client),
):
    started = perf_counter()
    cache = container.response_cache
    cacheable = cache is not None and request.temperature <= settings.CACHE_MAX_TEMPERATURE

    async def compute():
        result = await chat_service.generate(
            message=request.message,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return _parse_completion(result)

    try:
        if cacheable:
            payload, source = await cache.get_or_compute(
                _cache_key(request), compute
            )
        else:
            payload, source = await compute(), "bypass"

    except HTTPException:
        await _record(client, "completions", "error", started)
        raise

    except Exception as exc:
        await _record(client, "completions", "error", started)
        raise_mapped(exc)

    CACHE_REQUESTS.labels(result=source).inc()
    response.headers["X-Cache"] = source.upper()

    await _record(
        client, "completions", "success", started,
        cache=source, usage=payload.get("usage"),
    )

    return {
        "status": "success",
        "model": settings.LLAMA_MODEL,
        "response": payload["response"],
        "usage": payload.get("usage"),
    }


class _Lease:
    """
    Inference slot ka idempotent wrapper: release() kitni bhi baar call ho,
    slot sirf ek baar free hota hai.
    """

    def __init__(self):
        self._ctx = inference_controller.slot()
        self._held = False

    async def acquire(self):
        await self._ctx.__aenter__()
        self._held = True

    async def release(self):
        if self._held:
            self._held = False
            await self._ctx.__aexit__(None, None, None)


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    client: ClientInfo = Depends(get_client),
):
    started = perf_counter()

    # 1) Slot response start se pehle: queue full -> 429, timeout -> 504
    lease = _Lease()

    try:
        await lease.acquire()

    except Exception as exc:
        raise_mapped(exc)

    # 2) Pehla chunk bhi response start se pehle: provider errors -> 502/504/400
    try:
        if await http_request.is_disconnected():
            await lease.release()
            return Response(status_code=499)

        stream = chat_service.stream_generate_reserved(
            message=request.message,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        first_chunk = await stream.__anext__()

    except StopAsyncIteration:
        await lease.release()
        raise HTTPException(502, "Inference provider returned an empty stream")

    except BaseException as exc:
        await lease.release()

        if isinstance(exc, Exception):
            http_exc = map_error(exc)
            if http_exc is not None:
                raise http_exc from exc
        raise

    async def event_generator():
        outcome = "success"

        try:
            yield first_chunk

            async for chunk in stream:
                yield chunk

        except asyncio.CancelledError:
            outcome = "cancelled"
            raise

        except httpx.TimeoutException:
            outcome = "error"
            error = json.dumps({"detail": "Inference provider timed out"})
            yield f"event: error\ndata: {error}\n\n"

        except Exception:
            outcome = "error"
            logger.exception("Streaming inference failed")
            error = json.dumps({"detail": "Streaming inference failed"})
            yield f"event: error\ndata: {error}\n\n"

        finally:
            # Nested finally: aclose() cancel ho jaye tab bhi slot free hoga.
            try:
                await stream.aclose()
            finally:
                await lease.release()

            _fire(_record(client, "stream", outcome, started))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(lease.release),
    )
