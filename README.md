# MetaXpert Company AI

Private, self-hosted AI assistant for sales teams: multi-company document Q&A (RAG), a Super Admin portal,
and regulatory knowledge (US openFDA + eCFR Title 21, UK legislation.gov.uk, EU EUR-Lex) that is synced **only after an admin approves it**.
Runs on your own hardware with llama.cpp; no company document leaves the server.

> **Status: production-ready code, awaiting your verification.** Everything is on PostgreSQL when `DATABASE_URL` is set.
> Items marked unchecked in [docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md) (real Postgres run, HTTPS, service restart,
> backup restore, accuracy eval) must be ticked on your machine before onboarding real company data.

## Features
- **Multi-company isolation**: each company gets `co_<slug>__docs`; shared regulatory data lives in `reg__*` (read-only for companies). Deny by default.
- **Roles**: `admin` (Super Admin), `company_admin`, `user`. Scrypt password hashing, hashed session tokens, login rate limiting.
- **RAG chat** with streaming (SSE), citations, follow-up handling, stop/regenerate, per-user conversation history.
- **Regulatory sync**: openFDA + eCFR (21 CFR) + UK legislation.gov.uk + EU EUR-Lex → pending queue → admin approval → versioned, restorable documents. Questions that name a region (EU, UK, FDA/US) only search that region's `reg__*` collections. See `docs/REGULATORY_SOURCES.md`.
- **Operations**: request IDs, Prometheus metrics, usage report, backups, FIFO inference queue with per-user fairness.

## Architecture & Future Infrastructure Plan

**Ownership.** The application (this repository) is finished for a single-node pilot: `llama.cpp` + FastAPI + PostgreSQL/pgvector layout,
all features, tests, and Ubuntu deployment files. The **infrastructure / server team** (Proxmox, Ubuntu VM) owns: PostgreSQL + pgvector
setup, server load handling, GPU upgrade, the later move to `vLLM`, and Redis.

**Design goal.** Everything that changes with hardware is a setting or a deployment file, not application code:
| Future change | Done by | What changes | Application code |
|---|---|---|---|
| PostgreSQL + pgvector server | infra | `DATABASE_URL`, `RAG_STORE_BACKEND=pgvector`; `scripts/migrate_sqlite_to_postgres.py` for pilot data | none (all data layers have Postgres repositories) |
| Bigger GPU / more VRAM | infra | model path, `-c`, `-np`, `INFERENCE_MAX_CONCURRENT` in `deploy/ubuntu/companyai-chat.service` + `.env` | none |
| Second GPU | infra | second llama-server behind nginx upstream (`deploy/ubuntu/nginx-llama-upstream.conf`) | none |
| Switch to vLLM (OpenAI-compatible) | infra | `INFERENCE_PROVIDER=vllm`, `LLAMA_BASE_URL`, `LLAMA_MODEL` (= `--served-model-name`); example in `.env.example` | none: the `grammar` field is sent only to `llama_cpp` (`tests/test_provider_payload.py`) |
| Redis response cache | infra | `CACHE_BACKEND=redis`, `REDIS_URL` | none |
| Several API instances | engineering + infra | request queue, rate limiter and login limiter are in process memory and must move to Redis; `data/reg_archive` needs shared storage | **yes** (not done) |
| Different embedding model | infra | `RAG_EMBED_DIM`, truncate `rag_chunks`, re-ingest | none |

**Verdict (honest).** The software is modular and future-ready *for the rows marked "none"*: the inference provider is OpenAI-compatible,
storage is behind repository classes with SQLite and PostgreSQL implementations, and company isolation sits in one place (`app/core/tenancy.py`).
What is **not** yet future-ready is horizontal scaling of the API itself (in-memory queue/limiters), and the vLLM path itself is **not tested against a real vLLM server**.
Current capacity: one GTX 1070 = one inference slot, a pilot or one department (3-10 active users). Review notes for the reviewer: [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md); install steps: [docs/UBUNTU_DEPLOYMENT.md](docs/UBUNTU_DEPLOYMENT.md).

**Test status (maintainer's Windows machine).** `pytest tests -q -rs`: 254 passed, 12 skipped, 0 failed; `check_secrets.py --all` clean.
The 12 skips are the PostgreSQL tests (`TEST_DATABASE_URL` not set); they must be run once on a machine with Docker (`scripts\setup_postgres.ps1`) or on the Ubuntu VM before go-live.

## Backend Engineering & Concurrency Model

This section summarises how the backend handles load, so a reviewer does not need to open `docs/ARCHITECTURE.md`. Every point names the file that implements it.

**1. Async FastAPI + SSE streaming.** The API is fully `async`: HTTP connections, calls to the model servers (`httpx`), embeddings and PostgreSQL (`asyncpg`) never block the event loop, and
answers stream token by token over Server-Sent Events (`app/api/streaming.py`). A slot is reserved *before* the response starts, so queue-full and timeout errors
are returned as proper HTTP errors instead of a half-finished stream. A client that disconnects cancels its inference and frees the slot.

**2. Bounded FIFO queue, per-user cap, backpressure** (`app/core/concurrency.py`, `app/core/fairness.py`, `app/api/errors.py`).
| Mechanism | Behaviour | Setting |
|---|---|---|
| Inference slots | at most N requests run on the model at once | `INFERENCE_MAX_CONCURRENT` (default 1) |
| FIFO wait queue | extra requests wait in arrival order; a finished request hands its slot directly to the next waiter | `INFERENCE_MAX_QUEUE_SIZE` (20) |
| Queue full | `429` + `Retry-After` | `RETRY_AFTER_SECONDS` |
| Waited too long | `504` | `INFERENCE_QUEUE_TIMEOUT` (60 s) |
| Model server down | `503` | |
| Per-user cap | one user can hold at most N requests (running + waiting) at a time, so a single user cannot fill the queue | `INFERENCE_MAX_PER_CLIENT` (2) |
Note on fairness: this is *FIFO plus a per-user cap*, not a weighted fair scheduler. It stops one user from starving others by flooding, but a burst from many different users is served strictly in arrival order.
Queue depth, active requests, wait time and inference time are exported as Prometheus metrics (`app/core/metrics.py`, `/metrics`, protect with `METRICS_TOKEN`).

**3. Why one API worker + a central queue (hardware-aligned design).** On one GPU (GTX 1070) the bottleneck is GPU memory and compute, not Python CPU.
The queue, the per-user counters and the slot count live in the memory of one process, so a single uvicorn worker keeps them exactly consistent and never over-subscribes the GPU
(over-subscription is what causes out-of-memory crashes). More API workers would each run their own queue and defeat the limit.
When the GPU is upgraded and the model is served by vLLM, which batches requests continuously on the GPU, the expected change is to raise `INFERENCE_MAX_CONCURRENT` (and `INFERENCE_MAX_QUEUE_SIZE`) in `.env` to match the server's capacity.
The figure of 15-30 parallel requests is **an expectation, not a measurement**: the infrastructure team must confirm it with `scripts/load_test.py` on the real hardware. The same applies to the vLLM path itself (not yet tested against a real vLLM server).
Scaling to several API processes is the one thing that needs code: queue, limiters and counters must move to Redis (see the plan above).

**4. Caching, rate limiting, connection pooling.**
- **Response cache** (`app/core/cache.py`): TTL cache with `CACHE_BACKEND=memory | redis | none`, and single-flight (identical concurrent prompts share one model call).
  Used only by plain `/chat/completions`, where no company documents are in the prompt, so it cannot leak data between companies (`tests/test_cache_isolation.py` enforces this).
- **Rate limiting** (`app/core/ratelimit.py`): per-client token bucket (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`) returning `429` + `Retry-After`; separate stricter limits for login attempts. In memory, so per process.
- **PostgreSQL pooling** (`app/database/postgres.py`): one shared `asyncpg` pool (min 1, max 10 connections) for vectors, accounts, sessions, conversations, regulatory records and usage. Schema creation is serialised with an advisory lock so several starting processes cannot race.

## Super Admin portal (what is included)
- **Overview**: companies, users online, usage per client, pending regulatory updates (no document content is shown).
- **Companies**: create, edit (name, email, user limit), disable/enable (disabling logs out all its users immediately).
- **Users**: create super admin / company admin / user, reset password, disable. Company admins manage only their own company's users.
- **Documents**: list per company collection, view chunks/pages, upload, delete.
- **Regulatory**: EU/UK/AU/... versioned documents (upload, archive, restore, retire, view text); openFDA product codes and eCFR parts
  (21 CFR 801/807/820/878 by default) with *Check now* -> pending queue -> approve/reject.
  UK (UK MDR 2002) and EU (MDR 2017/745) law sync the same way, article by article, through the API
  (`/api/v1/admin/regulatory/law/{uk|eu}/*`, one inbox at `/api/v1/admin/regulatory/pending`); the admin portal screen for them is not built yet.
- **Audit log** (super admin, `GET /api/v1/admin/audit`): who did what and when: sign-ins and failed sign-ins (with IP), password changes/resets, user and company create/edit/disable, document add/delete, attempts to open another company's collection (`access.denied`), sign-out, and every regulatory action (sync, approve, reject, version upload/restore/retire). Stored in the same database (PostgreSQL or SQLite); passwords and document/chat content are never recorded; entries older than `AUDIT_RETENTION_DAYS` (default 365, 0 = keep forever) are purged at startup. Writing an audit entry never fails the request (errors are logged).
- Not included yet: permanent deletion of users/companies (they are disabled, not deleted).

## Architecture (short)
```
Browser ──HTTPS──> Caddy ──> FastAPI (1 worker) ──> llama.cpp chat   (:8081)
 React SPA          │             │ ──────────────> llama.cpp embed  (:8082)
                    └ static      ├──> PostgreSQL + pgvector  (vectors, accounts, sessions, conversations, regulatory, usage)
                                  └──> data/reg_archive (regulatory files); SQLite only if DATABASE_URL empty (dev)
```
Details and known limits: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project layout
```
app/            FastAPI backend (api/, core/, database/, providers/, rag/, services/)
frontend/       React + Vite SPA
tests/          pytest suite (backend)         frontend/test/  node:test suite
scripts/        create_company.py, create_user.py, preflight.py, check_secrets.py, check_live_sources.py,
                setup_postgres.ps1, migrate_sqlite_to_postgres.py, migrate_json_to_pgvector.py,
                pg_backup.ps1, pg_restore_test.ps1, backup.py, install_services.ps1 (NSSM), lock_ports.ps1,
                rag_eval.py, load_test.py, start_all.ps1, watchdog.ps1 ...
deploy/         Caddyfile (Docker), Caddyfile.host (Windows host), Dockerfile.web
docs/           API.md, ARCHITECTURE.md, DEPLOYMENT.md, SECURITY.md, PRODUCTION_CHECKLIST.md, PROJECT_STATE.md
eval/sample/    tiny pipeline check (NOT an accuracy benchmark)
eval/regulatory/ starter FDA/eCFR question set (verify with your regulatory lead)
Dockerfile, docker-compose.yml (production), docker-compose.db.yml (Postgres only), .env.example, .env.production.example
```

## Quick start (development, Windows/PowerShell)
```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
# models/ must contain the chat + embedding GGUF files, then:
.\scripts\start_all.ps1                         # chat model, embedding server, API (3 windows)
python scripts\create_company.py acme "Acme Surgical"
python scripts\create_user.py root --role admin
cd frontend ; npm install ; npm run dev         # http://localhost:5173  (proxies /api to :8000)
```
Tests: `python -m pytest tests -q -rs` and `cd frontend ; node --test test/`.

## Production
Ubuntu / Proxmox VM: [docs/UBUNTU_DEPLOYMENT.md](docs/UBUNTU_DEPLOYMENT.md). Architecture and capacity review for reviewers: [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md). Windows host: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). In short: `cp .env.production.example .env`, edit, run
`python scripts/preflight.py` (must show no `[ERROR]`), then `docker compose up -d --build`.
With `APP_ENV=production` the API **always requires authentication** (even before the first user exists).

## Configuration
All settings are environment variables / `.env` (see `app/core/config.py`). The important ones:

| Variable | Default | Production value |
|---|---|---|
| `APP_ENV` | `development` | `production` |
| `DATABASE_URL` | empty (SQLite) | `postgresql://company_ai:<pw>@host:5432/company_ai` (all data in Postgres) |
| `RAG_STORE_BACKEND` | `json` | `pgvector` (+ `DATABASE_URL`) |
| `RAG_EMBEDDER` | `hashing` (test only) | `llama_cpp` |
| `WEB_SEARCH_ENABLED` | `true` | `false` for multi-company |
| `METRICS_TOKEN` | empty (open) | long random token |
| `SESSION_TTL_HOURS` | `168` | `24` |
| `INFERENCE_MAX_CONCURRENT` | `1` | match your GPU (benchmark first) |

## Known limitations (be honest with users)
- One GPU / one worker: requests queue; in-memory limiter/cache/queue are per-process.
- With `DATABASE_URL` set, accounts, sessions, conversations, regulatory records, vectors and usage are all in PostgreSQL. Archived regulatory files stay in `data/reg_archive/` (backed up by `scripts/backup.py`).
- The Postgres repositories pass the same scenario tests as SQLite in the sandbox, but the real-PostgreSQL run is **yours to do** (`scripts\setup_postgres.ps1`).
- Answers come from a small local model: regulatory answers must be verified before they reach a customer.
- `LATIN_ONLY_OUTPUT=true` prevents Urdu-script answers (Roman Urdu is fine).
- openFDA / eCFR citations show the section (`21 CFR 807.87`, `510(k) K123456`) once those documents are re-approved/re-ingested; older chunks fall back to "Part N". UK/EU show `UK SI 2002/618 reg 5` / `EU Reg 2017/745 Art 10`.
- UK/EU sync was written and tested in a sandbox with mock responses only. Before relying on it run `python scripts/check_live_sources.py` on the server (it calls the real sites). EU consolidated text is informational (the Official Journal is authentic); UK text can lag very recent amendments.
- Since 2 Feb 2026 Part 820 is the QMSR with only six sections (820.1, .3, .7, .10, .35, .45); old numbers such as 820.30 no longer exist.

## Postgres migration (existing SQLite pilot data)
```powershell
.\scripts\setup_postgres.ps1                       # DB up + Postgres tests (must PASS, no skips)
python scripts\migrate_sqlite_to_postgres.py --dry-run
# stop the API, set DATABASE_URL + RAG_STORE_BACKEND=pgvector in .env, then:
python scripts\migrate_sqlite_to_postgres.py     # copies users, sessions, conversations, regulatory; compares counts
python scripts\migrate_json_to_pgvector.py --dry-run   # vectors (or simply re-ingest documents)
.\scripts\start_all.ps1
```
Idempotent (safe to re-run). `usage_log` is not copied. Keep `data/company_ai.db` until login and history are verified.

## Setup notes (pilot release)
- **PDF page numbers** are stored at upload time. Re-upload PDFs on a fresh server so citations show *Page N* instead of *Part N* (openFDA/eCFR records always show *Part N*).
- **System prompt rules**: if you use a custom `CHAT_SYSTEM_PROMPT` (`.env`) or `data/system_prompt.txt`, include: never add a "Note:" line about where the answer comes from, and never mention "the provided context" or "the documents provided".
- **Database / vector store**: set `RAG_STORE_BACKEND=pgvector` and `DATABASE_URL` when moving from local JSON/SQLite to PostgreSQL + pgvector.

## License
No license file yet: all rights reserved by the company until one is added.
