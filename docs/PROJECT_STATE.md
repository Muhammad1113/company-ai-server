# Company AI Server: Project State (naye chat mein ye paste karo)

> Updated 2026-11-14. Older notes below were written during phases 1-6; this header is the current truth.
> Roles: admin / company_admin / user (multi-company). Regulatory sync (openFDA + eCFR) with approval. Chat model: Qwen3.5-9B Q4_K_M (ctx 4096, np 1).
> Verified on the user's machine: backend `234 passed, 3 skipped` (skipped = Postgres integration, needs TEST_DATABASE_URL);
> frontend `node --test`: 46/46 after fixing one stale assertion (uncited document sources are now intentionally hidden).
> 2026-11-14 (later): accounts/sessions/conversations/regulatory have PostgreSQL repositories (app/database/pg_*.py), used when DATABASE_URL is set;
> migration script, parity + isolation tests, section-level FDA/eCFR citations, NSSM/firewall/backup scripts added. Sandbox-verified: SQLite parity scenarios,
> Postgres repo Python logic (fake pool), 49 frontend tests. NOT verified (needs your machine): real PostgreSQL/pgvector run, full pytest, HTTPS, services,
> backup restore, real-browser flow, accuracy on real questions.

## Stack
Python 3.11, FastAPI + uvicorn (1 worker), httpx, prometheus_client. Windows + PowerShell + .venv.
Chat inference: llama.cpp llama-server (Qwen3.5-9B-Q4_K_M, -ngl 99 -c 4096 -np 1, :8081) on GTX 1070.
Embeddings: llama-server --embedding (Qwen3-Embedding-0.6B-Q8_0, --pooling last, CPU, :8082).
Storage: SQLite + JSON vector store (default) ya PostgreSQL + pgvector (DATABASE_URL, RAG_STORE_BACKEND=pgvector).

## Layout
app/main.py                 app, lifespan, /health /ready(+db) /metrics, routers (auth dependency)
app/api/{deps,errors,streaming}.py  auth (API key YA session token)+rate limit, error mapping, SSE stream core (lease=per-client cap+GPU slot, prefetch, on_start/on_finish, preamble)
app/api/routes/             chat, conversations (CRUD + /{id}/chat stream/regenerate), auth (login/logout/me), admin (users), rag, usage
app/core/                   concurrency (FIFO queue), fairness (per-client cap), config, metrics, request_context, security, ratelimit, cache
app/providers/              BaseInferenceProvider, llama_cpp, factory (vLLM yahin plug hoga)
app/services/               chat_service, container (singletons: cache, limiter, db, usage_repo, rag_service)
app/database/               usage.py, accounts.py (users+sessions, scrypt), conversations.py (SQLite); postgres.py (usage only)
app/rag/                    chunker, extractors (txt/md/pdf/docx), embeddings (hashing|llama_cpp, query_prefix), store (JSON), pgvector_store, service
app/services/messages.py    system prompt + history truncation
frontend/                   React+Vite SPA (npm run build => frontend/dist; Caddy/Nginx serve karte hain). README: frontend/README.md
scripts/                    create_user.py, backup.py, start_all.ps1, smoke_test.ps1, rag_demo.ps1, rag_eval.py, usage_report.py
eval/sample/                4 docs + 8 questions (pipeline check)
deploy/                     nginx.conf, nginx-https.conf, Caddyfile, README.md
docker-compose.db.yml       postgres+pgvector ; docker-compose.monitoring.yml  prometheus+grafana+redis

## Decisions
- Concurrency 1 (GTX 1070). FIFO bounded queue; 429 full / 504 timeout / 502 provider. Slot response se PEHLE reserve.
- Disconnect => cancel => slot free (idempotent lease). Pure ASGI middleware.
- Cache: temperature <= 0.3 sirf; single-flight. Auth: API_KEYS="name:key[:role]" (khali => OFF). Roles user/admin.
- Postgres optional (asyncpg lazy import). pgvector: exact search by default (HNSW opt-in: filter ke baad lagta hai).
- Auth: API_KEYS="name:key[:role[:collections]]" ; collections=hr|finance (khali/* = sab). Admin hamesha sab. 403 (collection ka wajood nahi batate).
- File upload: raw body POST /rag/ingest-file?filename=..&collection=.. (admin). txt/md/pdf/docx; scanned PDF => 422 (OCR nahi hai). Limits: 20MB, 500 pages, 500k chars.
- Chat: stateless; client history bhejta hai (messages[]), server system prompt (CHAT_SYSTEM_PROMPT) lagata hai, purane turns budget par drop.
- Accounts: users+sessions (SQLite). Password scrypt; session token DB mein sirf hash. Auth ON hoti hai jab API_KEYS ya koi user ho.
- Conversations: server-side history, owner = username. /{id}/chat: user msg pehla chunk milne ke BAAD save (fail par kuch save nahi); assistant jawab stream ke baad (stopped bhi save).
- Fairness: INFERENCE_MAX_PER_CLIENT=2 (ek user queue na bhar sake) => 429.
- RAG in chat: /conversations/{id}/chat + collection => retrieval (pichla sawal + naya), prompt context, SSE `event: sources` pehle, sources DB mein. docs/API.md = frontend contract.
- Scale path: provider OpenAI-compatible (llama.cpp -> vLLM = base URL badlo). Queue/limiter/cache in-memory => multi-worker se pehle Redis; accounts/conversations ke Postgres versions baaqi.
- JSON vector store limit (sandbox benchmark, 1024-dim): 1k chunks 23ms/92MB, 5k 107ms/413MB, 10k 538ms/815MB => ~5k chunks se upar pgvector.
- Embedder/dimension guard: store embedder id + dim mismatch par startup error. Fail-fast dimension probe.
- State (queue/limiter/cache) per-process => abhi 1 uvicorn worker.

## Verified
- Live (user machine): 43 tests pass; smoke test (auth on): cache MISS/HIT, stream, cancellation (active 0, cancellations 1);
  RAG eval Qwen3-Embedding-0.6B on 8 sample questions: hit@1 1.0, hit@3 1.0, MRR 1.0, ~86 ms/search.
- Sandbox: Postgres code ke fake-pool tests (26 tests).
- User machine: 53 tests pass (+3 Postgres integration skipped: Docker nahi hai).
- Sandbox: extractors (asli PDF/DOCX), messages, ACL tests pass.
- User machine: 80 pass + 3 skipped (phase5). Phase6: sandbox par accounts/conversations/sse/backup tests pass.
## NOT yet verified
- frontend: sandbox mein sirf lib tests (21), JSX syntax, SSR render smoke test hue. `npm run build` aur browser mein asli chalna (login, stream, stop, regenerate, admin) user machine par pehli baar.
- tests/test_api_phase6.py (user machine), nginx/caddy configs, frontend.
- tests/test_api_phase5.py (user machine par chalana hai), real Postgres/pgvector, Redis, eval on real company docs (Urdu PDFs extraction quality!), load test with auth+cache.

## Next
0. Frontend: npm install + build + browser par test (neeche 'NOT yet verified'). GET /rag/documents backend mein nahi: admin panel mein document list nahi.
1. Real company docs ingest + eval (30-50 questions); compare BGE-M3 / query prefix / reranker. 2. OCR for scanned PDFs (agar zaroorat).
3. Nginx/Caddy TLS. 4. Hashed API keys in DB + admin usage overview. 5. Redis-backed limiter/cache + Postgres (multi-worker/server).
6. Linux + newer GPU => vLLM.
