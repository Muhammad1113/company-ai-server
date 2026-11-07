import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.concurrency import InferenceQueueFull, InferenceQueueTimeout

logger = logging.getLogger("company_ai.api")


def map_error(exc: Exception) -> HTTPException | None:
    """Queue/provider errors -> HTTP errors. None = unknown error."""

    if isinstance(exc, InferenceQueueFull):
        return HTTPException(
            status_code=429,
            detail="Server queue is full. Please retry shortly.",
            headers={"Retry-After": str(settings.RETRY_AFTER_SECONDS)},
        )

    if isinstance(exc, InferenceQueueTimeout):
        return HTTPException(504, "Queue wait timed out. Please retry.")

    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(504, "Inference provider timed out")

    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 400
    ):
        return HTTPException(400, "Request is too large for the model context")

    if isinstance(exc, httpx.HTTPError):
        logger.error("Provider HTTP error: %r", exc)
        return HTTPException(502, "Inference provider error")

    if isinstance(exc, (KeyError, IndexError, TypeError)):
        logger.error("Invalid provider response: %r", exc)
        return HTTPException(502, "Invalid response received from provider")

    if isinstance(exc, RuntimeError):
        logger.error("Provider unavailable: %r", exc)
        return HTTPException(503, "Inference provider is unavailable")

    return None


def raise_mapped(exc: Exception):
    http_exc = map_error(exc)
    if http_exc is None:
        raise exc
    raise http_exc from exc
