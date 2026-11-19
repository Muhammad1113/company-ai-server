# Deployment

Two supported layouts. In both, the GPU model servers run on the host (llama.cpp).

## A. Docker Compose (API + PostgreSQL + HTTPS web)
1. Start the model servers on the host, bound so the container can reach them
   (`.\scripts\start_all.ps1 -LlamaHost 0.0.0.0`) and **block ports 8081/8082 from the network with the firewall**
   (llama-server has no API key set; only the Docker host should reach it).
2. `cp .env.production.example .env` → set `POSTGRES_PASSWORD` (alphanumeric), `METRICS_TOKEN`, `SITE_ADDRESS`, `TLS_MODE`.
3. `python scripts/preflight.py` (needs `pip install -r requirements.txt`) → fix every `[ERROR]`.
4. `docker compose up -d --build`
5. Create the first accounts (the SQLite files live in the Docker volume `appdata`, mounted at `/app/data`):
   ```
   docker compose exec api python scripts/create_company.py acme "Acme Surgical"
   docker compose exec api python scripts/create_user.py root --role admin --db /app/data/company_ai.db
   ```
   Users created this way work immediately (no restart needed in production mode).
6. Check: `https://<SITE_ADDRESS>/health`, then log in.

## B. Windows host (current pilot machine)
1. `docker compose -f docker-compose.db.yml up -d` (PostgreSQL + pgvector on 127.0.0.1:5432).
2. `.env`: copy `.env.production.example`, set `DATABASE_URL=postgresql://company_ai:<password>@127.0.0.1:5432/company_ai`,
   `RAG_STORE_BACKEND=pgvector`, `APP_ENV=production`.
3. Move an existing JSON store: `python scripts\migrate_json_to_pgvector.py --dry-run`, then run it with `--map old=co_<company>__docs`.
   The embedder must be the one that built the JSON store, otherwise re-ingest.
4. `.\scripts\start_all.ps1` (uvicorn already runs with `--proxy-headers`). Put Caddy (or nginx) in front for HTTPS.
5. Use `scripts\watchdog.ps1` or a Windows service wrapper (NSSM) so the API and models restart after a crash/reboot.

## C. Hardening on the Windows host (in this order)
1. `.\scripts\setup_postgres.ps1` → Postgres tests PASS, none skipped. Then migrate (README, "Postgres migration").
2. `.\scripts\lock_ports.ps1 -Verify`, then `-Apply` (`-DockerMode` if the API runs in Docker).
3. HTTPS: `caddy validate --config deploy\Caddyfile.host`; set `SITE_ADDRESS`, `TLS_MODE`, `FRONTEND_DIST` (forward slashes). `TLS_MODE=internal` needs Caddy's root certificate trusted on every client (`caddy trust` / group policy); a public DNS name plus an email gives Let's Encrypt instead.
4. Services: `.\scripts\install_services.ps1 -Nssm <path> -LlamaServer <path> -Caddy <path>`; then kill the API and reboot once to prove auto-restart.
5. `python scripts\check_live_sources.py`, then approve the first eCFR/openFDA syncs in the portal.
6. `python scripts\check_secrets.py --all` and `gitleaks` on git history before the repo is shared.
7. `.\scripts\pg_backup.ps1`, then `.\scripts\pg_restore_test.ps1` (must print RESTORE TEST PASS). Schedule `pg_backup.ps1` daily (Task Scheduler).

## Backups and restore
- `python scripts/backup.py` copies `data/` (SQLite files if any, `reg_archive/` originals, `system_prompt.txt`). Schedule it (Task Scheduler/cron).
  In Docker: `docker compose exec api python scripts/backup.py` then `docker compose cp api:/app/backups ./backups` (move it off the server).
- PostgreSQL: `.\scripts\pg_backup.ps1` and `.\scripts\pg_restore_test.ps1` (restores into a scratch DB, compares counts). Production compose: `-ComposeFile docker-compose.yml`.
- Real restore: stop the API, `docker cp` the dump into the container, `pg_restore -U company_ai -d company_ai --clean --if-exists /tmp/restore.dump`.
- **Test a restore** on a spare machine once before the pilot; an untested backup is not a backup.
- Backups contain company documents and password hashes: store them encrypted and off the server.

## Upgrades
`git pull` → `python -m pytest tests -q -rs` → `docker compose up -d --build`. Take a backup first.
