import math

from fastapi import Depends, HTTPException, Request

from app.core.metrics import AUTH_FAILURES, RATE_LIMITED
from app.core.security import ClientInfo, authenticate, extract_api_key
from app.services import container


async def get_client(request: Request) -> ClientInfo:
    """Authentication + rate limiting. FastAPI ise per-request ek hi baar chalata hai."""

    accounts = container.accounts_repo

    # Auth tab ON hai jab API keys hon YA koi user account bana ho
    if container.api_keys or accounts.has_users:
        token = extract_api_key(request.headers)
        info = authenticate(token, container.api_keys) if container.api_keys else None

        if info is None and accounts.has_users:
            user = await accounts.get_session_user(token)
            if user is not None:
                info = ClientInfo(
                    name=user["username"],
                    role=user["role"],
                    collections=(
                        frozenset(user["collections"]) if user["collections"] else None
                    ),
                    company=user["company"],
                )

        if info is None:
            AUTH_FAILURES.inc()
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing credentials",
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


def ensure_collection_access(client: ClientInfo, collection: str) -> None:
    """Collection ka wajood/na-wajood na batao: sirf 403."""
    if not client.can_access(collection):
        raise HTTPException(status_code=403, detail="No access to this collection")


def ensure_collection_manage(client: ClientInfo, collection: str) -> None:
    """Upload / delete / list-for-admin: super admin sab; company_admin sirf apni company ki."""
    if not client.can_manage(collection):
        raise HTTPException(status_code=403, detail="No permission to manage this collection")


async def require_admin(client: ClientInfo = Depends(get_client)) -> ClientInfo:
    """Super admin only."""
    if not client.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return client


async def require_company_admin(client: ClientInfo = Depends(get_client)) -> ClientInfo:
    """Company ka apna admin (company ke bina nahi). Super admin company routes se bahar rehta hai."""
    if not client.is_company_admin:
        raise HTTPException(status_code=403, detail="Company admin role required")
    return client
