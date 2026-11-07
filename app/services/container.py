"""
Saare shared singletons ek jagah. Routes `container.x` attribute access
karte hain (import-time copy nahi), taake tests monkeypatch kar saken.
"""
import logging

from app.core.cache import MemoryTTLCache, RedisCache, ResponseCache
from app.core.config import settings
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import parse_api_keys
from app.database.postgres import PostgresDatabase, PostgresUsageRepository
from app.database.usage import UsageRepository
from app.rag.embeddings import HashingEmbedder, LlamaCppEmbedder
from app.rag.pgvector_store import PgVectorStore
from app.rag.service import RagService
from app.rag.store import JsonVectorStore
from app.services.chat_service import chat_service

logger = logging.getLogger("company_ai.container")


def _build_cache():
    backend = settings.CACHE_BACKEND.lower()
    if backend == "none":
        return None
    if backend == "memory":
        store = MemoryTTLCache(max_entries=settings.CACHE_MAX_ENTRIES)
    elif backend == "redis":
        store = RedisCache(settings.REDIS_URL)
    else:
        raise ValueError(f"Unsupported CACHE_BACKEND: {backend}")
    return ResponseCache(store, ttl=settings.CACHE_TTL_SECONDS)


def _build_embedder():
    kind = settings.RAG_EMBEDDER.lower()
    if kind == "hashing":
        return HashingEmbedder(settings.RAG_HASHING_DIM)
    if kind == "llama_cpp":
        return LlamaCppEmbedder(
            settings.RAG_EMBED_BASE_URL,
            settings.RAG_EMBED_MODEL,
            query_prefix=settings.RAG_QUERY_PREFIX,
        )
    raise ValueError(f"Unsupported RAG_EMBEDDER: {kind}")


def _build_store(embedder):
    backend = settings.RAG_STORE_BACKEND.lower()

    if backend == "json":
        return JsonVectorStore(settings.RAG_STORE_PATH, embedder.id)

    if backend == "pgvector":
        if db is None:
            raise ValueError("RAG_STORE_BACKEND=pgvector needs DATABASE_URL")
        dim = getattr(embedder, "dim", None) or settings.RAG_EMBED_DIM
        return PgVectorStore(
            db, embedder.id, dim, hnsw=settings.RAG_PGVECTOR_HNSW
        )

    raise ValueError(f"Unsupported RAG_STORE_BACKEND: {backend}")


def _build_rag():
    embedder = _build_embedder()
    return RagService(
        embedder,
        _build_store(embedder),
        chat_service.generate,
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        top_k=settings.RAG_TOP_K,
        max_context_chars=settings.RAG_MAX_CONTEXT_CHARS,
    )


api_keys = parse_api_keys(settings.API_KEYS)

rate_limiter = (
    TokenBucketLimiter(
        settings.RATE_LIMIT_PER_MINUTE, settings.RATE_LIMIT_BURST
    )
    if settings.RATE_LIMIT_PER_MINUTE > 0
    else None
)

response_cache = _build_cache()

db = PostgresDatabase(settings.DATABASE_URL) if settings.DATABASE_URL else None

usage_repo = (
    PostgresUsageRepository(db) if db else UsageRepository(settings.DATABASE_PATH)
)

rag_service = _build_rag()


async def _probe_embedding_dimension():
    """Fail fast: embedding model ki dimension aur pgvector column match karein."""
    store_dim = getattr(rag_service.store, "dim", None)
    if store_dim is None:
        return

    try:
        vector = await rag_service.embedder.embed_query("dimension probe")
    except Exception as exc:
        logger.warning(
            "embedding server not reachable at startup (%r); "
            "skipping dimension check", exc
        )
        return

    if len(vector) != store_dim:
        raise RuntimeError(
            f"Embedding model returns {len(vector)} dimensions but "
            f"RAG_EMBED_DIM={store_dim}"
        )


async def startup():
    if db is not None:
        await db.connect()

    await usage_repo.init()

    init_store = getattr(rag_service.store, "init", None)
    if init_store is not None:
        await init_store()

    await _probe_embedding_dimension()

    if not api_keys:
        logger.warning(
            "API_KEYS is empty: authentication is DISABLED (development mode)"
        )


async def shutdown():
    closers = [
        response_cache.close if response_cache else None,
        rag_service.close,
        usage_repo.close,
        db.close if db else None,        # sab se aakhir mein
    ]
    for closer in closers:
        if closer is None:
            continue
        try:
            await closer()
        except Exception:
            logger.exception("error during shutdown")
