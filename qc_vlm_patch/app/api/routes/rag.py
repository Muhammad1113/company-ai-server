import asyncio
import re
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import ensure_collection_access, ensure_collection_manage, get_client, require_admin
from app.api.errors import raise_mapped
from app.core import tenancy
from app.core.config import settings
from app.core.metrics import RAG_QUERIES
from app.core.security import ClientInfo
from app.rag import vision
from app.rag.extractors import ExtractionError, extract_text
from app.services import audit, container

router = APIRouter(prefix="/rag", tags=["RAG"])

_ID = r"^[\w.\-:]+$"


class IngestRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=200, pattern=_ID)
    text: str = Field(min_length=1, max_length=settings.RAG_MAX_DOC_CHARS)
    collection: str = Field(default="default", max_length=100, pattern=_ID)
    metadata: dict[str, str] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    collection: str = Field(default="default", max_length=100, pattern=_ID)
    top_k: int = Field(default=settings.RAG_TOP_K, ge=1, le=10)
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_tokens: int = Field(default=300, ge=1, le=1024)


@router.post("/ingest")
async def ingest(request: IngestRequest, client: ClientInfo = Depends(get_client)):
    ensure_collection_manage(client, request.collection)
    try:
        result = await container.rag_service.ingest(
            request.doc_id, request.text, request.collection, request.metadata
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise_mapped(exc)
    await audit.record(client, "document.ingest", target=f"{request.collection}/{request.doc_id}",
                       detail={"chars": len(request.text)})
    return result


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    collection: str = Field(default="default", max_length=100, pattern=_ID)
    top_k: int = Field(default=settings.RAG_TOP_K, ge=1, le=20)


async def _read_limited(request: Request, limit_bytes: int) -> bytes:
    """Upload body ko limit ke saath padho (memory DoS se bachne ke liye)."""
    mb = limit_bytes / (1024 * 1024)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit_bytes:
        raise HTTPException(413, f"File exceeds the {mb:g} MB limit")

    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit_bytes:
            raise HTTPException(413, f"File exceeds the {mb:g} MB limit")
        chunks.append(chunk)

    return b"".join(chunks)


def _doc_id_from(filename: str) -> str:
    stem = PurePath(filename).stem
    return re.sub(r"[^\w.\-:]", "_", stem)[:200] or "document"


@router.post("/ingest-file")
async def ingest_file(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    collection: str = Query(default="default", max_length=100, pattern=_ID),
    doc_id: str | None = Query(default=None, max_length=200, pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    """
    Raw body upload (.txt .md .pdf .docx):
      curl.exe -X POST "http://host/api/v1/rag/ingest-file?filename=policy.pdf&collection=hr"
               -H "Authorization: Bearer KEY" --data-binary "@policy.pdf"
    """
    ensure_collection_manage(client, collection)      # body padhne se PEHLE (bina permission ke upload nahi)
    data = await _read_limited(
        request, int(settings.RAG_MAX_UPLOAD_MB * 1024 * 1024)
    )

    use_vlm = settings.VLM_MODE != "off" and PurePath(filename).suffix.lower() == ".pdf"

    try:
        text, meta = await asyncio.to_thread(
            extract_text,
            filename,
            data,
            max_pages=settings.RAG_MAX_PDF_PAGES,
            max_chars=settings.RAG_MAX_FILE_CHARS,
            allow_scanned=use_vlm,
        )
    except ExtractionError as exc:
        raise HTTPException(exc.status, str(exc))

    vlm_stats = None
    if use_vlm:
        try:
            text, vlm_stats = await vision.enrich_pdf(data, text)
        except vision.VisionError as exc:
            raise HTTPException(exc.status, str(exc))
        meta["chars"] = str(len(text))

    final_id = doc_id or _doc_id_from(filename)
    try:
        result = await container.rag_service.ingest(final_id, text, collection, meta)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise_mapped(exc)
    await audit.record(client, "document.ingest", target=f"{collection}/{final_id}",
                       detail={"chars": int(meta["chars"]), "type": meta.get("type"), **(vlm_stats or {})})

    return {
        **result,
        "type": meta["type"],
        "chars": int(meta["chars"]),
        "pages": int(meta["pages"]) if "pages" in meta else None,
        "empty_pages": int(meta["empty_pages"]) if "empty_pages" in meta else None,
        "vision": vlm_stats,
    }


@router.post("/search")
async def search(request: SearchRequest, client: ClientInfo = Depends(get_client)):
    """Retrieval only: GPU/LLM use nahi hota. Eval aur debugging ke liye."""
    ensure_collection_access(client, request.collection)
    try:
        hits = await container.rag_service.search(
            request.question, request.collection, request.top_k
        )
    except Exception as exc:
        raise_mapped(exc)

    return {
        "embedder": container.rag_service.embedder.id,
        "hits": [
            {
                "doc_id": h["doc_id"],
                "chunk_index": h["chunk_index"],
                "score": h["score"],
                "preview": h["text"][:200],
            }
            for h in hits
        ],
    }


@router.post("/query")
async def query(request: QueryRequest, client: ClientInfo = Depends(get_client)):
    ensure_collection_access(client, request.collection)
    try:
        result = await container.rag_service.query(
            request.question,
            request.collection,
            request.top_k,
            request.temperature,
            request.max_tokens,
        )
    except Exception as exc:
        raise_mapped(exc)

    RAG_QUERIES.labels(llm_called=str(result["llm_called"]).lower()).inc()
    return result


@router.get("/documents")
async def list_documents(
    collection: str = Query(default="default", max_length=100, pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    """Admin panel: collection ke documents (delete button ke liye). Khali collection => khali list."""
    ensure_collection_manage(client, collection)
    try:
        docs = await container.rag_service.list_documents(collection)
    except Exception as exc:
        raise_mapped(exc)
    return {"collection": collection, "documents": docs}


@router.get("/documents/{doc_id}/content")
async def document_content(
    doc_id: str,
    collection: str = Query(default="default", max_length=100, pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    """Admin panel "View": document ka text (parts mein). Sirf wohi jo is collection ko manage kar sakta hai."""
    ensure_collection_manage(client, collection)
    try:
        parts = await container.rag_service.get_document_chunks(doc_id, collection)
    except Exception as exc:
        raise_mapped(exc)
    if not parts:
        raise HTTPException(404, "Document not found")
    return {"doc_id": doc_id, "collection": collection, "parts": [{"index": i + 1, "text": t} for i, t in enumerate(parts)]}


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    collection: str = Query(default="default", max_length=100, pattern=_ID),
    client: ClientInfo = Depends(get_client),
):
    ensure_collection_manage(client, collection)
    removed = await container.rag_service.delete(doc_id, collection)
    if removed == 0:
        raise HTTPException(404, "Document not found")
    await audit.record(client, "document.delete", target=f"{collection}/{doc_id}", detail={"chunks_removed": removed})
    return {"doc_id": doc_id, "collection": collection, "chunks_removed": removed}


@router.get("/stats")
async def stats(_: ClientInfo = Depends(require_admin)):
    return await container.rag_service.stats()


@router.get("/collections")
async def collections(client: ClientInfo = Depends(get_client)):
    """Frontend dropdown: jin collections tak is user ki access hai."""
    stats = await container.rag_service.stats()
    counts = {n: d for n, d in stats["collections"].items() if client.can_access(n)}
    if client.company:                       # company ki apni collection khali ho tab bhi dikhao (upload ke liye)
        counts.setdefault(tenancy.company_collection(client.company), 0)
    return {
        "collections": [
            {"name": name, "documents": docs,
             "scope": "shared" if tenancy.is_shared(name) else "company" if tenancy.owner_of(name) else "legacy",
             "can_manage": client.can_manage(name)}
            for name, docs in sorted(counts.items())
        ]
    }
