"""
Audit helper. `await audit.record(actor, "user.create", target="ali", detail={...})`.
Best-effort: audit likhne mein masla aaye to request fail NAHI hoti (error log hota hai).
Repo init na hui ho (kuch tests / dev) to chupchap kuch nahi karta. Passwords ya content kabhi detail mein na dalo.
"""
import logging

from app.services import container

logger = logging.getLogger("company_ai.audit")


async def record(actor, action: str, *, target=None, detail=None, ok: bool = True, ip=None,
                 role: str | None = None, company: str | None = None) -> None:
    repo = getattr(container, "audit_repo", None)
    try:
        if repo is None or not repo.ready:
            return
        if isinstance(actor, str):
            name = actor
        else:                                    # ClientInfo
            name, role, company = actor.name, actor.role, actor.company
        await repo.record(name[:64], role, company, action, target, detail, ip, ok)
    except Exception:
        logger.exception("audit write failed (action=%s)", action)
