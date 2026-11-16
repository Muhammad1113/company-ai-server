"""
Super admin: regulatory documents (versions/archive), openFDA aur eCFR (21 CFR) sync ka approval.
Company users is router tak pahunch hi nahi sakte (require_admin). Neeche sab kuch public regulatory data hai.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.api.errors import raise_mapped
from app.api.routes.rag import _read_limited
from app.core.config import settings
from app.core.security import ClientInfo
from app.rag.extractors import ExtractionError
from app.services import audit, container
from app.services.regulatory import RegulatoryError

router = APIRouter(prefix="/admin/regulatory", tags=["Regulatory"], dependencies=[Depends(require_admin)])

_COLLECTION = r"^reg__[\w.\-:]+$"


def _svc():
    return container.regulatory_service


async def _guard(coro):
    try:
        return await coro
    except RegulatoryError as exc:
        raise HTTPException(400, str(exc))
    except ExtractionError as exc:
        raise HTTPException(exc.status, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise_mapped(exc)


# ---------- versioned documents ----------
@router.get("/versions")
async def list_versions(collection: str | None = Query(default=None, max_length=100, pattern=_COLLECTION)):
    return {"versions": await container.regulatory_repo.list_versions(collection)}


@router.post("/versions", status_code=201)
async def upload_version(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    collection: str = Query(max_length=100, pattern=_COLLECTION),
    doc_key: str = Query(min_length=1, max_length=100),
    version: str = Query(min_length=1, max_length=40),
    effective_date: str | None = Query(default=None, max_length=10),
    source_url: str | None = Query(default=None, max_length=500),
    note: str | None = Query(default=None, max_length=500),
    archive_only: bool = False,
    admin: ClientInfo = Depends(require_admin),
):
    """Raw body upload (jaise /rag/ingest-file). Naya active version aate hi purana archive ho jata hai."""
    data = await _read_limited(request, int(settings.RAG_MAX_UPLOAD_MB * 1024 * 1024))
    result = await _guard(_svc().upload_version(
        collection=collection, doc_key=doc_key, version=version, filename=filename, data=data,
        effective_date=effective_date or None, source_url=source_url or None, note=note,
        by=admin.name, make_active=not archive_only))
    await audit.record(admin, "regulatory.version_upload", target=f"{collection}/{doc_key}@{version}",
                       detail={"archive_only": archive_only})
    return result


@router.get("/versions/{version_id}/text")
async def version_text(version_id: int):
    return await _guard(_svc().version_text(version_id))


@router.post("/versions/{version_id}/restore")
async def restore_version(version_id: int, admin: ClientInfo = Depends(require_admin)):
    result = await _guard(_svc().restore(version_id, admin.name))
    await audit.record(admin, "regulatory.version_restore", target=f"version:{version_id}")
    return result


@router.post("/versions/{version_id}/retire")
async def retire_version(version_id: int, admin: ClientInfo = Depends(require_admin)):
    result = await _guard(_svc().retire(version_id))
    await audit.record(admin, "regulatory.version_retire", target=f"version:{version_id}")
    return result


# ---------- openFDA ----------
class CodeBody(BaseModel):
    code: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z0-9]{3}$")
    label: str | None = Field(default=None, max_length=120)


@router.get("/openfda")
async def openfda_status():
    return await _svc().openfda_status()


@router.post("/openfda/codes", status_code=201)
async def add_code(body: CodeBody, admin: ClientInfo = Depends(require_admin)):
    await _guard(_svc().add_code(body.code, body.label))
    await audit.record(admin, "regulatory.fda_code_add", target=body.code.upper())
    return {"code": body.code.upper()}


@router.delete("/openfda/codes/{code}")
async def remove_code(code: str, admin: ClientInfo = Depends(require_admin)):
    if not re.match(r"^[A-Za-z0-9]{3}$", code):
        raise HTTPException(404, "Code not found")
    if not await container.regulatory_repo.remove_code(code.upper()):
        raise HTTPException(404, "Code not found")
    await audit.record(admin, "regulatory.fda_code_remove", target=code.upper())
    return {"removed": code.upper()}


@router.post("/openfda/sync")
async def sync_now(admin: ClientInfo = Depends(require_admin)):
    """FDA se check karo. Sirf 'pending' banta hai; approve hone tak kuch bhi RAG mein nahi jata."""
    result = await _guard(_svc().run_sync())
    await audit.record(admin, "regulatory.fda_sync", detail={"changes": result.get("changes")})
    return result


@router.get("/openfda/pending")
async def list_pending(history: bool = False):
    return {"updates": await container.regulatory_repo.list_pending(include_decided=history, source="openfda")}


@router.get("/openfda/pending/{pending_id}")
async def get_pending(pending_id: int):
    item = await container.regulatory_repo.get_pending(pending_id)
    if item is None:
        raise HTTPException(404, "Update not found")
    return item


@router.post("/openfda/pending/{pending_id}/approve")
async def approve(pending_id: int, admin: ClientInfo = Depends(require_admin)):
    result = await _guard(_svc().approve(pending_id, admin.name))
    await audit.record(admin, "regulatory.approve", target=f"pending:{pending_id}", detail={"approved": result.get("approved")})
    return result


@router.post("/openfda/pending/{pending_id}/reject")
async def reject(pending_id: int, admin: ClientInfo = Depends(require_admin)):
    result = await _guard(_svc().reject(pending_id, admin.name))
    await audit.record(admin, "regulatory.reject", target=f"pending:{pending_id}", detail={"rejected": result.get("rejected")})
    return result


# ---------- eCFR (21 CFR) ----------
# Approve/reject/detail wohi pending endpoints hain (/openfda/pending/{id}/...): service pending.source dekh kar sahi raasta leta hai.
class PartBody(BaseModel):
    part: str = Field(min_length=1, max_length=4, pattern=r"^[0-9]{1,4}$")
    label: str | None = Field(default=None, max_length=120)


@router.get("/ecfr")
async def ecfr_status():
    return await _guard(_svc().ecfr_status())


@router.post("/ecfr/parts", status_code=201)
async def add_part(body: PartBody, admin: ClientInfo = Depends(require_admin)):
    await _guard(_svc().add_part(body.part, body.label))
    await audit.record(admin, "regulatory.cfr_part_add", target=f"21 CFR {body.part}")
    return {"part": body.part}


@router.delete("/ecfr/parts/{part}")
async def remove_part(part: str, admin: ClientInfo = Depends(require_admin)):
    if not re.match(r"^[0-9]{1,4}$", part):
        raise HTTPException(404, "Part not found")
    if not await container.regulatory_repo.remove_part(part):
        raise HTTPException(404, "Part not found")
    await audit.record(admin, "regulatory.cfr_part_remove", target=f"21 CFR {part}")
    return {"removed": part}


@router.post("/ecfr/sync")
async def ecfr_sync_now(admin: ClientInfo = Depends(require_admin)):
    """eCFR se check karo. Sirf 'pending' banta hai; approve hone tak kuch bhi RAG mein nahi jata."""
    result = await _guard(_svc().run_ecfr_sync())
    await audit.record(admin, "regulatory.ecfr_sync", detail={"changes": result.get("changes")})
    return result


@router.get("/ecfr/pending")
async def ecfr_list_pending(history: bool = False):
    return {"updates": await container.regulatory_repo.list_pending(include_decided=history, source="ecfr")}
