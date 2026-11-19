"""
Company admin: apni company ke users sambhalo. Company slug hamesha login se aata hai (URL/body se kabhi nahi),
isliye ek company ka admin doosri company tak pahunch hi nahi sakta.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_company_admin
from app.core import tenancy
from app.core.security import ClientInfo
from app.services import audit, container

router = APIRouter(prefix="/company", tags=["Company"])

_NAME = r"^[A-Za-z0-9_.@-]{3,64}$"


class NewUser(BaseModel):
    username: str = Field(pattern=_NAME)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["user"] = "user"           # company_admin sirf super admin banata hai


class EditUser(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    disabled: bool | None = None


@router.get("")
async def my_company(client: ClientInfo = Depends(require_company_admin)):
    company = await container.accounts_repo.get_company(client.company)
    users = await container.accounts_repo.list_users(client.company)
    return {**company, "collection": tenancy.company_collection(client.company), "users": users}


@router.post("/users", status_code=201)
async def create_user(body: NewUser, client: ClientInfo = Depends(require_company_admin)):
    try:
        result = await container.accounts_repo.create_user(
            body.username, body.password, "user", None, client.company
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await audit.record(client, "user.create", target=body.username, detail={"role": "user", "company": client.company})
    return result


@router.patch("/users/{username}")
async def edit_user(username: str, body: EditUser, client: ClientInfo = Depends(require_company_admin)):
    target = next((u for u in await container.accounts_repo.list_users(client.company)
                   if u["username"].lower() == username.lower()), None)
    if target is None:
        raise HTTPException(404, "User not found")
    if target["role"] != "user" and target["username"].lower() != client.name.lower():
        raise HTTPException(403, "Only a super admin can change another company admin")
    if body.disabled and target["username"].lower() == client.name.lower():
        raise HTTPException(400, "You cannot disable your own account")
    try:
        found = await container.accounts_repo.update_user(
            username, password=body.password, disabled=body.disabled, only_company=client.company
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not found:
        raise HTTPException(404, "User not found")
    action = ("user.password_reset" if body.password else "user.disable" if body.disabled is True else
              "user.enable" if body.disabled is False else "user.update")
    await audit.record(client, action, target=username, detail={"fields": sorted(body.model_fields_set)})
    return {"status": "updated", "username": username}
