from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_client, require_admin
from app.api.errors import raise_mapped
from app.core.config import settings
from app.core.metrics import RAG_QUERIES
from app.core.security import ClientInfo
from app.services import container

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
async def ingest(request: IngestRequest, _: ClientInfo = Depends(require_admin)):
    try:
        return await container.rag_service.ingest(
            request.doc_id, request.text, request.collection, request.metadata
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise_mapped(exc)


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    collection: str = Field(default="default", max_length=100, pattern=_ID)
    top_k: int = Field(default=settings.RAG_TOP_K, ge=1, le=20)


@router.post("/search")
async def search(request: SearchRequest, _: ClientInfo = Depends(get_client)):
    """Retrieval only: GPU/LLM use nahi hota. Eval aur debugging ke liye."""
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
async def query(request: QueryRequest, _: ClientInfo = Depends(get_client)):
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


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    collection: str = "default",
    _: ClientInfo = Depends(require_admin),
):
    removed = await container.rag_service.delete(doc_id, collection)
    if removed == 0:
        raise HTTPException(404, "Document not found")
    return {"doc_id": doc_id, "collection": collection, "chunks_removed": removed}


@router.get("/stats")
async def stats(_: ClientInfo = Depends(require_admin)):
    return await container.rag_service.stats()
