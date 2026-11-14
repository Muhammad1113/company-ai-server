from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.core.concurrency import inference_controller
from app.core.security import ClientInfo
from app.database.accounts import _KEEP
from app.services import container

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])

_NAME = r"^[A-Za-z0-9_.@-]{3,64}$"
_COLLECTION = r"^[\w.\-:]+$"


_SLUG = r"^[a-z0-9]+(?:_[a-z0-9]+)*$"


class CreateCompany(BaseModel):
    slug: str = Field(min_length=2, max_length=32, pattern=_SLUG)
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    max_users: int = Field(default=0, ge=0, le=10000)       # 0 = unlimited


class UpdateCompany(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    disabled: bool | None = None
    max_users: int | None = Field(default=None, ge=0, le=10000)


class CreateUser(BaseModel):
    username: str = Field(pattern=_NAME)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["user", "company_admin", "admin"] = "user"
    company: str | None = Field(default=None, max_length=32, pattern=_SLUG)   # user/company_admin ke liye zaroori
    # Sirf company ke baghair legacy accounts ke liye; company users ke liye ignore
    collections: list[str] | None = Field(default=None, max_length=50)


class UpdateUser(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["user", "company_admin", "admin"] | None = None
    disabled: bool | None = None
    # Field bheja hi na ho => badlega nahi; null => khali (koi extra nahi); list => explicit (sirf legacy users)
    collections: list[str] | None = Field(default=None, max_length=50)


# ---------- companies (super admin) ----------
@router.post("/companies", status_code=201)
async def create_company(body: CreateCompany):
    try:
        return await container.accounts_repo.create_company(
            body.slug, body.name, body.email, body.max_users
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/companies")
async def list_companies():
    """Har company: users, is waqt online (pichle 5 min), uski collection."""
    return {"companies": await container.accounts_repo.list_companies()}


@router.get("/companies/{slug}")
async def get_company(slug: str):
    company = await container.accounts_repo.get_company(slug)
    if company is None:
        raise HTTPException(404, "Company not found")
    users = await container.accounts_repo.list_users(slug)
    return {**company, "users": users}


@router.patch("/companies/{slug}")
async def update_company(slug: str, body: UpdateCompany):
    email = body.email if "email" in body.model_fields_set else _KEEP
    try:
        found = await container.accounts_repo.update_company(
            slug, name=body.name, email=email, disabled=body.disabled, max_users=body.max_users
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not found:
        raise HTTPException(404, "Company not found")
    return {"status": "updated", "slug": slug}


@router.post("/users", status_code=201)
async def create_user(body: CreateUser, admin: ClientInfo = Depends(require_admin)):
    try:
        return await container.accounts_repo.create_user(
            body.username, body.password, body.role, body.collections, body.company
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/users")
async def list_users(company: str | None = None):
    return {"users": await container.accounts_repo.list_users(company)}


@router.patch("/users/{username}")
async def update_user(username: str, body: UpdateUser):
    collections = body.collections if "collections" in body.model_fields_set else _KEEP
    try:
        found = await container.accounts_repo.update_user(
            username,
            password=body.password,
            role=body.role,
            disabled=body.disabled,
            collections=collections,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if not found:
        raise HTTPException(404, "User not found")
    return {"status": "updated", "username": username}


# ---------- load / online overview (super admin) ----------
@router.get("/overview")
async def overview(hours: float = Query(default=24, gt=0, le=24 * 30)):
    """
    Super admin dashboard: companies, online users, requests/latency, GPU queue.
    PRIVACY: sirf ginti, naam aur timing. Sawal-jawab ka content is endpoint se kabhi nahi milta.
    """
    repo = container.accounts_repo
    companies = await repo.list_companies()
    users = await repo.list_users()
    online = await repo.list_online()
    usage = await container.usage_repo.by_client(hours)

    company_of = {u["username"].lower(): u["company"] for u in users}
    per_company = {c["slug"]: {"requests": 0, "errors": 0, "lat_sum": 0.0, "lat_n": 0,
                               "prompt_tokens": 0, "completion_tokens": 0} for c in companies}
    other = {"requests": 0, "errors": 0}
    for row in usage:
        slug = company_of.get(str(row["client"]).lower())
        bucket = per_company.get(slug) if slug else None
        if bucket is None:                       # super admin, API keys, purane users
            other["requests"] += row["requests"]
            other["errors"] += row["errors"]
            continue
        bucket["requests"] += row["requests"]
        bucket["errors"] += row["errors"]
        bucket["prompt_tokens"] += row["prompt_tokens"]
        bucket["completion_tokens"] += row["completion_tokens"]
        if row["avg_latency_ms"] is not None:    # weighted average
            bucket["lat_sum"] += row["avg_latency_ms"] * row["requests"]
            bucket["lat_n"] += row["requests"]

    out = []
    for c in companies:
        b = per_company[c["slug"]]
        out.append({
            **c,
            "requests": b["requests"], "errors": b["errors"],
            "avg_latency_ms": round(b["lat_sum"] / b["lat_n"], 1) if b["lat_n"] else None,
            "prompt_tokens": b["prompt_tokens"], "completion_tokens": b["completion_tokens"],
        })

    ic = inference_controller
    return {
        "window_hours": hours,
        "companies": out,
        "online": online,
        "other_requests": other,
        "queue": {
            "active": ic.active, "max_concurrent": ic.max_concurrent,
            "waiting": len(ic.waiters), "max_queue_size": ic.max_queue_size,
        },
        "pending_regulatory": await container.regulatory_repo.count_pending(),   # openFDA updates jo approval maangte hain
        "totals": {
            "companies": len(companies), "users": len(users), "online": len(online),
            "requests": sum(x["requests"] for x in out) + other["requests"],
        },
    }
