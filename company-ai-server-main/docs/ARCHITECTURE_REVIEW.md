# Architecture review (for the infrastructure / security reviewer)

Honest assessment of what this system is, what it can carry, and where it stops scaling. Numbers marked *estimate* were not measured:
run `python scripts/load_test.py` on the target VM and replace them.

## 1. What it is
Browser (React SPA) → Caddy (HTTPS) → FastAPI (1 uvicorn worker) → llama.cpp chat (:8081) and llama.cpp embeddings (:8082);
PostgreSQL + pgvector holds vectors, accounts, sessions, conversations, regulatory records and usage.
Multi-company isolation is enforced in the API layer by collection name (`co_<slug>__*`, shared read-only `reg__*`), deny by default,
and every vector query filters on `collection`. Regulatory text (openFDA, eCFR) enters the knowledge base only after super-admin approval.

## 2. Capacity on a GTX 1070 (8 GB VRAM)
- Qwen3.5-9B Q4_K_M weights are roughly 5.5–6 GB (*estimate*); with context 4096 and one slot that leaves little headroom. Do not raise `-c` or `-np` on this card.
- Inference concurrency is **1**; extra requests wait in a FIFO queue (max 20, 60 s timeout, then HTTP 429/504). Per-user cap is 2 active requests.
- A typical answer is tens of seconds on this card (*estimate*): roughly 2–6 answers per minute. That suits **a few to ~10 people actively chatting** (a pilot or one department),
  not a whole company at once. Embedding runs on CPU and is not the bottleneck for chat.
- Context is 4096 tokens: long documents are answered from retrieved chunks (top 4), not read in full.

## 3. Scaling path (what changes, what doesn't)
| Step | Change needed | Code change |
|---|---|---|
| Faster/larger single GPU | model path, `-c`, `-np`, `INFERENCE_MAX_CONCURRENT` | none |
| Second GPU | second llama-server behind an nginx `least_conn` upstream | none (`deploy/ubuntu/nginx-llama-upstream.conf`) |
| vLLM on newer GPUs | `INFERENCE_PROVIDER=vllm`, base URL, model name | none expected, **untested** |
| More than one API instance | Redis for limiter/cache/queue, shared storage for `data/reg_archive` | yes (queue and limiters are in process memory) |
| PostgreSQL high availability | replica/managed Postgres | none |
| Different embedding model | new `RAG_EMBED_DIM`, truncate `rag_chunks`, re-ingest | none |

## 4. Strengths
- Data never leaves the server; web search is disabled in production mode (preflight errors if it is on).
- Isolation is tested at repository, HTTP and (on a real Postgres) pgvector level; session tokens are stored hashed; scrypt passwords; login and request rate limits.
- Stateless API apart from the queue, so the path to more GPUs is mostly configuration.
- Backups have a scripted restore test.

## 5. Weaknesses / risks (be explicit with stakeholders)
1. **Single API worker and single Postgres**: no high availability; a VM or Postgres failure is an outage.
2. **In-memory queue, limiter, cache**: lost on restart; blocks multi-instance deployment until Redis.
3. **Isolation is application-level**: a bug in one route could leak across companies (mitigated by tests). Postgres row-level security is not used.
4. **Small local model**: it can state wrong regulatory facts confidently. Answers carry citations (section level for FDA/eCFR) but must be verified before reaching a customer.
5. **Audit log exists** (admin/security events, see SECURITY.md) but is stored in the same database and is not tamper-proof; **no encryption at rest** (use LUKS on the VM disk); API keys in `.env` are plaintext.
6. **Not yet verified on real infrastructure**: Postgres run of the new repositories, HTTPS, GPU service restarts, backup restore, accuracy on real questions (see `docs/PRODUCTION_CHECKLIST.md`).
7. Proxmox: the GPU is bound to one VM; VM snapshots do not replace database backups.

## 6. What the reviewer should verify
- `tests/test_isolation_http.py` and `tests/test_pg_isolation_http.py` pass; read `app/core/tenancy.py` and `app/api/deps.py`.
- `python scripts/preflight.py` has no `[ERROR]`; `ufw status`; ports 5432/8000/8081/8082 closed from the LAN.
- Load test numbers on the real VM; queue/429 behaviour under 10+ concurrent users.
- Backup and restore test output; reboot test.
