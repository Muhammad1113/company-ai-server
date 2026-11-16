import math

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_client
from app.core.metrics import LOGIN_FAILURES
from app.core import tenancy
from app.core.security import ClientInfo, extract_api_key
from app.services import audit, container

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _public(client: ClientInfo) -> dict:
    return {
        "username": client.name,
        "role": client.role,
        "company": client.company,
        # Frontend upload/query ke liye: company ki apni collection + shared regulatory
        "default_collection": (
            tenancy.company_collection(client.company) if client.company else None
        ),
        "collections": sorted(client.collections) if client.collections else None,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    host = request.client.host if request.client else "unknown"

    # Brute-force se bachao: per-IP aur per-username (sahi/ghalat dono attempts gine jate hain)
    for key in (f"ip:{host}", f"user:{body.username.lower()}"):
        allowed, retry_after = container.login_limiter.allow(key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Try again shortly.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    user = await container.accounts_repo.authenticate(body.username, body.password)

    if user is None:
        LOGIN_FAILURES.inc()
        await audit.record(body.username, "auth.login_failed", ok=False, ip=host)
        raise HTTPException(401, "Invalid username or password")

    token, expires_at = await container.accounts_repo.create_session(user["id"])
    await audit.record(user["username"], "auth.login", ip=host, role=user["role"], company=user["company"])

    return {
        "token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "username": user["username"],
            "role": user["role"],
            "company": user["company"],
            "default_collection": (
                tenancy.company_collection(user["company"]) if user["company"] else None
            ),
            "collections": user["collections"],
        },
    }


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, request: Request, client: ClientInfo = Depends(get_client)
):
    """Naya session token wapas aata hai (baqi sab sessions band ho jate hain)."""
    allowed, retry_after = container.login_limiter.allow(f"pw:{client.name.lower()}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again shortly.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )

    accounts = container.accounts_repo
    try:
        user_id = await accounts.change_password(
            client.name, body.current_password, body.new_password
        )
    except ValueError as exc:
        await audit.record(client, "auth.password_change_failed", ok=False,
                           ip=request.client.host if request.client else None)
        raise HTTPException(400, str(exc))

    token, expires_at = await accounts.create_session(user_id)
    await audit.record(client, "auth.password_change", ip=request.client.host if request.client else None)
    return {"token": token, "token_type": "bearer", "expires_at": expires_at}


@router.post("/logout")
async def logout(request: Request, client: ClientInfo = Depends(get_client)):
    await container.accounts_repo.delete_session(extract_api_key(request.headers))
    await audit.record(client, "auth.logout", ip=request.client.host if request.client else None)
    return {"status": "ok"}


@router.get("/me")
async def me(client: ClientInfo = Depends(get_client)):
    return _public(client)
