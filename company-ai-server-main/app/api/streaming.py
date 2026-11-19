"""
Streaming core (chat aur conversations dono). Pehle jo chat.py mein tha:
- Slot response start se PEHLE reserve (429/504), pehla chunk bhi pehle (502/504/400)
- Disconnect => cancel => slot free (idempotent lease)
- on_start: pehle chunk ke BAAD (persist user message), on_finish: generation ke baad
"""
import asyncio
import json
import logging
from time import perf_counter

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api.errors import map_error, raise_mapped
from app.core.concurrency import inference_controller
from app.core.request_context import request_id_var
from app.core.security import ClientInfo
from app.services import container
from app.services.chat_service import chat_service
from app.services.sse import SseTextCollector

logger = logging.getLogger(__name__)

_background: set = set()


def fire(coro):
    """Fire-and-forget (cancel ke dauran bhi kaam na khoye)."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def record_usage(client, endpoint, status, started, cache=None, usage=None):
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


class _Lease:
    """Inference slot ka idempotent wrapper: release() kitni bhi baar, slot ek baar free."""

    def __init__(self, client_name: str):
        self._ctx = inference_controller.slot()
        self._held = False
        self._client = client_name
        self._client_held = False

    def _release_client(self):
        if self._client_held:
            self._client_held = False
            container.client_limiter.release(self._client)

    async def acquire(self):
        container.client_limiter.acquire(self._client)      # ClientBusy => 429
        self._client_held = True
        try:
            await self._ctx.__aenter__()
            self._held = True
        except BaseException:
            self._release_client()
            raise

    async def release(self):
        try:
            if self._held:
                self._held = False
                await self._ctx.__aexit__(None, None, None)
        finally:
            self._release_client()


async def _safely(coro_fn, *args):
    try:
        await coro_fn(*args)
    except Exception:
        logger.exception("post-stream task failed")


async def stream_chat(
    http_request: Request,
    client: ClientInfo,
    prepared: list[dict],
    temperature: float,
    max_tokens: int,
    endpoint: str = "stream",
    on_start=None,
    on_finish=None,
    preamble: str | None = None,
):
    """
    on_start(): async, pehla chunk milne ke baad (fail ho to request fail).
    on_finish(text, outcome): async, outcome = success | cancelled | error.
    """
    started = perf_counter()

    # 1) Slot response start se pehle: queue full -> 429, timeout -> 504
    lease = _Lease(client.name)

    try:
        await lease.acquire()

    except Exception as exc:
        raise_mapped(exc)

    # 2) Pehla chunk bhi response start se pehle
    stream = None

    try:
        if await http_request.is_disconnected():
            await lease.release()
            return Response(status_code=499)

        stream = chat_service.stream_generate_reserved(
            message=None,
            messages=prepared,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        first_chunk = await stream.__anext__()

        if on_start is not None:
            await on_start()

    except StopAsyncIteration:
        await lease.release()
        raise HTTPException(502, "Inference provider returned an empty stream")

    except BaseException as exc:
        try:
            if stream is not None:
                await stream.aclose()          # on_start fail ho to provider connection band
        except BaseException:
            pass
        await lease.release()

        if isinstance(exc, Exception):
            http_exc = map_error(exc)
            if http_exc is not None:
                raise http_exc from exc
        raise

    collector = SseTextCollector()
    collector.feed(first_chunk)

    async def event_generator():
        outcome = "success"

        try:
            if preamble:
                yield preamble                 # jaise "event: sources" (generation shuru hone ke baad)
            yield first_chunk

            async for chunk in stream:
                collector.feed(chunk)
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

            tasks = [_safely(record_usage, client, endpoint, outcome, started)]
            if on_finish is not None:
                tasks.append(_safely(on_finish, collector.text, outcome))

            for task in tasks:
                if outcome == "cancelled":
                    fire(task)                 # cancel ke dauran await dobara cancel ho sakta hai
                else:
                    await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(lease.release),
    )
