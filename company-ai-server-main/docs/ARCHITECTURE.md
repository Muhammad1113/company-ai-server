# Architecture

## Request flow (chat with documents)
1. `POST /api/v1/conversations/{id}/chat` → `get_client` (auth + rate limit) → collection access check (`tenancy.can_read`).
2. Retrieval: query embedding (llama.cpp embed) → vector search **filtered by collection** (pgvector or JSON store).
3. A slot in the FIFO inference queue is reserved *before* streaming starts (429 queue full / 504 timeout / 502 model down).
4. Tokens stream back as SSE; the answer and its sources are saved to the user's conversation.

## Tenancy model (`app/core/tenancy.py`)
| Collection | Who can read | Who can write |
|---|---|---|
| `co_<company>__*` | users of that company, super admin | company_admin of that company, super admin |
| `reg__*` (shared regulatory) | every authenticated user | super admin only |
| anything else (legacy) | super admin, or explicit API-key list | super admin |

Isolation is by collection name enforced in the API layer; the vector tables hold all companies in one table
(`rag_chunks.collection`). Every search/delete query filters on `collection`.

## Storage
| Data | Where |
|---|---|
| Document chunks + vectors | PostgreSQL/pgvector (`RAG_STORE_BACKEND=pgvector`) or `data/rag_store.json` (dev, <~5k chunks) |
| Usage log | PostgreSQL if `DATABASE_URL`, else SQLite |
| Users, sessions, companies, conversations, regulatory versions/snapshots/pending | PostgreSQL if `DATABASE_URL` (`app/database/pg_*.py`), else SQLite `DATABASE_PATH` (dev only) |
| Archived regulatory files | `data/reg_archive/` |

## Scaling limits (read before growing)
- Queue, rate limiter, cache and login limiter live in process memory → **run exactly one uvicorn worker** until they move to Redis.
- Data is in PostgreSQL, but the in-memory pieces above still pin you to one worker. Next for multiple API instances: Redis for limiter/cache/queue, and `data/reg_archive` on shared storage.
- Response cache keys contain no company. That is safe only because the cache is used solely by plain `/chat/completions` (no documents in the prompt). `tests/test_cache_isolation.py` fails if anyone adds it to a RAG path.
- Inference concurrency is bound by GPU memory (`INFERENCE_MAX_CONCURRENT`, llama.cpp `-np`).
