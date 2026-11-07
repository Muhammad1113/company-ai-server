# Company AI Server: Project State (naye chat mein ye paste karo)

## Stack
Python 3.11, FastAPI + uvicorn (1 worker), httpx, prometheus_client. Windows + PowerShell + .venv.
Chat inference: llama.cpp llama-server (Qwen3-4B-Q4_K_M, -ngl 99 -c 4096 -np 1, :8081) on GTX 1070.
Embeddings: llama-server --embedding (Qwen3-Embedding-0.6B-Q8_0, --pooling last, CPU, :8082).
Storage: SQLite + JSON vector store (default) ya PostgreSQL + pgvector (DATABASE_URL, RAG_STORE_BACKEND=pgvector).

## Layout
app/main.py                 app, lifespan, /health /ready(+db) /metrics, routers (auth dependency)
app/api/{deps,errors}.py    auth+rate limit, error mapping (429/504/502/400/503)
app/api/routes/             chat (completions+cache, stream SSE), rag (ingest/search/query/delete/stats), usage
app/core/                   concurrency (FIFO queue), config, metrics, request_context, security, ratelimit, cache
app/providers/              BaseInferenceProvider, llama_cpp, factory (vLLM yahin plug hoga)
app/services/               chat_service, container (singletons: cache, limiter, db, usage_repo, rag_service)
app/database/               usage.py (SQLite), postgres.py (PostgresDatabase + PostgresUsageRepository)
app/rag/                    chunker, embeddings (hashing|llama_cpp, query_prefix), store (JSON), pgvector_store, service
scripts/                    smoke_test.ps1, rag_demo.ps1, rag_eval.py, usage_report.py
eval/sample/                4 docs + 8 questions (pipeline check)
docker-compose.db.yml       postgres+pgvector ; docker-compose.monitoring.yml  prometheus+grafana+redis

## Decisions
- Concurrency 1 (GTX 1070). FIFO bounded queue; 429 full / 504 timeout / 502 provider. Slot response se PEHLE reserve.
- Disconnect => cancel => slot free (idempotent lease). Pure ASGI middleware.
- Cache: temperature <= 0.3 sirf; single-flight. Auth: API_KEYS="name:key[:role]" (khali => OFF). Roles user/admin.
- Postgres optional (asyncpg lazy import). pgvector: exact search by default (HNSW opt-in: filter ke baad lagta hai).
- Embedder/dimension guard: store embedder id + dim mismatch par startup error. Fail-fast dimension probe.
- State (queue/limiter/cache) per-process => abhi 1 uvicorn worker.

## Verified
- Live (user machine): 43 tests pass; smoke test (auth on): cache MISS/HIT, stream, cancellation (active 0, cancellations 1);
  RAG eval Qwen3-Embedding-0.6B on 8 sample questions: hit@1 1.0, hit@3 1.0, MRR 1.0, ~86 ms/search.
- Sandbox: Postgres code ke fake-pool tests (26 tests).
## NOT yet verified
- Real Postgres/pgvector run (tests/test_postgres_integration.py), Redis backend, eval on real company docs, load test with auth+cache.

## Next
1. Postgres integration test + switch. 2. Eval on real docs (30-50 questions), compare BGE-M3, query prefix, reranker.
3. Redis-backed limiter/cache. 4. Hashed API keys in DB + admin endpoints. 5. Nginx (TLS, limits). 6. Linux + newer GPU => vLLM.
