"""
Saare shared singletons ek jagah. Routes `container.x` attribute access
karte hain (import-time copy nahi), taake tests monkeypatch kar saken.
"""
import asyncio
import logging

from app.core.cache import MemoryTTLCache, RedisCache, ResponseCache
from app.core.config import settings
from app.core.fairness import ClientConcurrencyLimiter
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import parse_api_keys
from app.database.accounts import AccountsRepository
from app.database.conversations import ConversationRepository
from app.database.pg_accounts import PgAccountsRepository
from app.database.pg_conversations import PgConversationRepository
from app.database.pg_regulatory import PgRegulatoryRepository
from app.database.postgres import PostgresDatabase, PostgresUsageRepository
from app.database.regulatory import RegulatoryRepository
from app.database.usage import UsageRepository
from app.rag.embeddings import HashingEmbedder, LlamaCppEmbedder
from app.rag.pgvector_store import PgVectorStore
from app.rag.service import RagService
from app.rag.store import JsonVectorStore
from app.services.agent import Agent, make_llm_classifier
from app.services.chat_service import chat_service
from app.services.ecfr import ECFRClient
from app.services.openfda import OpenFDAClient
from app.services.regulatory import RegulatoryService
from app.services.websearch import WebSearch

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
        min_score=settings.RAG_MIN_SCORE,
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


def _build_agent():
    if not settings.AGENT_ENABLED:
        return None
    web = None
    if settings.WEB_SEARCH_ENABLED:
        split = lambda v: tuple(x.strip() for x in v.split(",") if x.strip())  # noqa: E731
        web = WebSearch(
            max_results=settings.WEB_SEARCH_MAX_RESULTS,
            timeout=settings.WEB_SEARCH_TIMEOUT,
            cache_ttl=settings.WEB_SEARCH_CACHE_TTL,
            blocked_domains=split(settings.WEB_SEARCH_BLOCKED_DOMAINS),
            providers=split(settings.WEB_SEARCH_PROVIDERS),
            searxng_url=settings.SEARXNG_URL,
            proxy=settings.WEB_SEARCH_PROXY,
            contact=settings.WEB_SEARCH_CONTACT,
            privacy_terms=settings.WEB_SEARCH_PRIVACY_TERMS,
            per_user_per_minute=settings.WEB_SEARCH_PER_USER_PER_MINUTE,
            global_per_minute=settings.WEB_SEARCH_GLOBAL_PER_MINUTE,
            max_concurrent=settings.WEB_SEARCH_MAX_CONCURRENT,
            cooldown_base=settings.WEB_SEARCH_COOLDOWN_SECONDS,
        )
    classify = (
        make_llm_classifier(chat_service.generate, settings.AGENT_CLASSIFIER_TIMEOUT)
        if settings.AGENT_CLASSIFIER_ENABLED else None
    )
    return Agent(web, timezone=settings.AGENT_TIMEZONE, policy=settings.WEB_SEARCH_POLICY, classify=classify)


agent = _build_agent()

# DATABASE_URL set => accounts, sessions, conversations aur regulatory records bhi PostgreSQL mein.
# Khali => SQLite file (DATABASE_PATH): sirf development / chhota single-machine pilot.
if db is not None:
    accounts_repo = PgAccountsRepository(db, session_ttl_hours=settings.SESSION_TTL_HOURS)
    conversations_repo = PgConversationRepository(db)
    regulatory_repo = PgRegulatoryRepository(db)
else:
    accounts_repo = AccountsRepository(settings.DATABASE_PATH, session_ttl_hours=settings.SESSION_TTL_HOURS)
    conversations_repo = ConversationRepository(settings.DATABASE_PATH)
    regulatory_repo = RegulatoryRepository(settings.DATABASE_PATH)
regulatory_service = RegulatoryService(
    regulatory_repo,
    rag_service,
    settings.REG_ARCHIVE_DIR,
    OpenFDAClient(settings.OPENFDA_BASE_URL, settings.OPENFDA_API_KEY, settings.OPENFDA_LIMIT, settings.OPENFDA_TIMEOUT),
    max_codes=settings.OPENFDA_MAX_CODES,
    max_pages=settings.RAG_MAX_PDF_PAGES,
    max_chars=settings.RAG_MAX_FILE_CHARS,
    ecfr_client=ECFRClient(settings.ECFR_BASE_URL, settings.ECFR_TITLE, settings.ECFR_TIMEOUT),
    max_parts=settings.ECFR_MAX_PARTS,
)
_auto_sync_task = None
client_limiter = ClientConcurrencyLimiter(settings.INFERENCE_MAX_PER_CLIENT)
login_limiter = TokenBucketLimiter(
    settings.LOGIN_ATTEMPTS_PER_MINUTE, settings.LOGIN_BURST
)


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
    await accounts_repo.init()
    await conversations_repo.init()
    await regulatory_repo.init()

    init_store = getattr(rag_service.store, "init", None)
    if init_store is not None:
        await init_store()

    await _probe_embedding_dimension()

    global _auto_sync_task
    if settings.OPENFDA_AUTO_SYNC_HOURS > 0:
        _auto_sync_task = asyncio.create_task(regulatory_service.auto_loop(settings.OPENFDA_AUTO_SYNC_HOURS))

    if settings.APP_ENV.strip().lower() == "production":
        from app.core.production import production_issues
        for level, msg in production_issues(settings):
            (logger.error if level == "error" else logger.warning)("production check: %s", msg)

    if not api_keys and not accounts_repo.has_users:
        logger.warning(
            "No API_KEYS and no user accounts: authentication is DISABLED "
            "(development mode). Create a user: python scripts/create_user.py"
        )


async def _close_web():
    agent.web.close()


async def _stop_auto_sync():
    if _auto_sync_task is not None:
        _auto_sync_task.cancel()


async def shutdown():
    closers = [
        _stop_auto_sync,
        (lambda: _close_web()) if agent and agent.web else None,
        response_cache.close if response_cache else None,
        rag_service.close,
        usage_repo.close,
        accounts_repo.close,
        conversations_repo.close,
        regulatory_repo.close,
        db.close if db else None,        # sab se aakhir mein
    ]
    for closer in closers:
        if closer is None:
            continue
        try:
            await closer()
        except Exception:
            logger.exception("error during shutdown")
