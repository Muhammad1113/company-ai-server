import math

from fastapi import Depends, HTTPException, Request

from app.core.metrics import AUTH_FAILURES, RATE_LIMITED
from app.core.security import ClientInfo, authenticate, extract_api_key
from app.services import container


async def get_client(request: Request) -> ClientInfo:
    """Authentication + rate limiting. FastAPI ise per-request ek hi baar chalata hai."""

    if container.api_keys:
        info = authenticate(
            extract_api_key(request.headers), container.api_keys
        )
        if info is None:
            AUTH_FAILURES.inc()
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        # Dev mode: auth off, har IP alag client
        host = request.client.host if request.client else "unknown"
        info = ClientInfo(name=f"anon:{host}", role="admin")

    limiter = container.rate_limiter
    if limiter is not None:
        allowed, retry_after = limiter.allow(info.name)
        if not allowed:
            RATE_LIMITED.inc()
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    request.state.client = info.name
    return info


async def require_admin(client: ClientInfo = Depends(get_client)) -> ClientInfo:
    if not client.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return client
