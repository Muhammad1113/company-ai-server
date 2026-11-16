# Changelog

## Production hardening (unreleased)
- `APP_ENV=production`: authentication fail-closed (no open admin mode); session lookup works for users created by script without restart.
- `METRICS_TOKEN` for `/metrics`; `app/core/production.py` + `scripts/preflight.py` + startup warnings for unsafe settings.
- Docker: `Dockerfile`, `docker-compose.yml` (API + pgvector + Caddy), `deploy/Caddyfile` (HTTPS, headers, CSP, SSE-safe proxy).
- `requirements.txt` split into runtime / dev; removed Windows-only and test-only packages from runtime.
- CI workflow (backend with Postgres service, frontend tests + build); `.gitignore`, `.dockerignore`, `.env*.example`.
- Frontend: fixed stale `planSources` test to match the citation patch.
- Docs: README, ARCHITECTURE, DEPLOYMENT, SECURITY, PRODUCTION_CHECKLIST.
- `start_all.ps1`: `--proxy-headers`, `-LlamaHost` parameter.
- Conversations: `updated_at` is now strictly increasing on every message and list order has a `rowid` tie-break
  (fixes flaky `test_title_helpers_and_recency_order` on Windows, where the clock ticks in ~15 ms steps).
