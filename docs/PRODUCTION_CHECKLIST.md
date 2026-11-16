# Production checklist

Tick every box before real company data goes in. "Verified" means you saw it work, not that it is written in a doc.

## Code & tests
- [ ] `python -m pytest tests -q -rs` → 0 failed
- [ ] `.\scripts\setup_postgres.ps1` → `test_postgres_integration`, `test_repo_parity` (pg) and `test_pg_isolation_http` **run and pass**, none skipped
- [ ] `python scripts\migrate_sqlite_to_postgres.py` counts match (if you have pilot data); log in as each role afterwards
- [ ] `node --test test/` and `npm run build` pass; click through login, stream, stop, regenerate, admin, regulatory approval in a real browser
- [ ] Company A cannot see Company B: search, chat, documents list, delete, conversations, admin endpoints (on the **pgvector** backend)
- [ ] XSS check: make the model output `<img src=x onerror=alert(1)>`; nothing executes

## Configuration
- [ ] `APP_ENV=production`, `python scripts/preflight.py` shows no `[ERROR]`
- [ ] `RAG_STORE_BACKEND=pgvector`, `RAG_EMBEDDER=llama_cpp`, `WEB_SEARCH_ENABLED=false`
- [ ] `METRICS_TOKEN` set; `SESSION_TTL_HOURS` reviewed
- [ ] Audit log checked in the portal (Admin, Audit log): create a test user, reset its password, fail a login; confirm entries appear and no password shows
- [ ] `python scripts\check_secrets.py --all` clean; no secret in git history (`gitleaks`/`trufflehog`); repository is private

## Network
- [ ] HTTPS works with a certificate users' browsers trust
- [ ] `.\scripts\lock_ports.ps1 -Verify` clean; from ANOTHER machine 8081/8082/5432 fail and 443 works
- [ ] Login works from the real client network (rate limiter sees real client IPs, not the proxy's)

## Data & operations
- [ ] `pg_backup.ps1` scheduled **and `pg_restore_test.ps1` printed RESTORE TEST PASS**; a copy lives off this machine
- [ ] `install_services.ps1` run; API killed → back within ~15 s; one full reboot → everything up without logging in
- [ ] Disk space/log rotation checked; `/ready` monitored

## Answer quality (the real go/no-go)
- [ ] `python scripts\check_live_sources.py` OK; eCFR 801/807/820/878 + your FDA codes synced and approved
- [ ] 30–50 real questions (start from `eval/regulatory/questions.jsonl`) verified by your regulatory/sales lead; citations show the right section; target agreed in writing
- [ ] Real company PDFs (including Urdu/scanned ones) ingested and spot-checked
- [ ] Users told: AI answers must be verified before quoting to a customer; regulatory answers cite the source
- [ ] Pilot limited to 3–5 users, feedback channel agreed, review date set
