import asyncio
import contextvars
import logging
import re
import uuid
from time import perf_counter

request_id_var = contextvars.ContextVar("request_id", default="-")

logger = logging.getLogger("company_ai.http")

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")
_QUIET_PATHS = {"/metrics", "/health"}


class RequestIDFilter(logging.Filter):
    """Har log line mein request_id add karta hai (kisi bhi module se)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format=(
            "%(asctime)s | %(levelname)s | %(name)s | "
            "rid=%(request_id)s | %(message)s"
        ),
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIDFilter())


class RequestContextMiddleware:
    """
    Pure ASGI middleware. BaseHTTPMiddleware ki jagah, kyunke wo
    streaming + client disconnect ke saath masle karta hai.

    - X-Request-ID set/echo karta hai
    - request_id contextvar set karta hai (sab logs mein aata hai)
    - request ABHI KHATAM hone par log karta hai (streaming ki poori
      duration, disconnect/cancel par bhi)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = dict(scope["headers"]).get(b"x-request-id", b"")
        incoming = _SAFE_ID.sub("", raw.decode("latin-1"))[:64]
        rid = incoming or uuid.uuid4().hex

        token = request_id_var.set(rid)
        started = perf_counter()
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)

        except asyncio.CancelledError:
            raise

        except Exception:
            status = status or 500
            logger.exception("unhandled error")
            raise

        finally:
            if scope["path"] not in _QUIET_PATHS:
                logger.info(
                    "%s %s status=%s duration=%.3fs",
                    scope["method"],
                    scope["path"],
                    status or "-",
                    perf_counter() - started,
                )
            request_id_var.reset(token)
