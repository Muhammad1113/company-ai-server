# Security model

## In place
- Passwords: scrypt (per-user salt); session tokens stored only as SHA-256 hashes; constant-time comparisons.
- Login brute-force limiting per IP and per username; general per-client rate limit; per-user inference cap.
- Router-level auth on every API router; deny-by-default collection access; 403 (not 404) for foreign collections.
- `APP_ENV=production`: authentication is always required (fail-closed).
- `METRICS_TOKEN` protects `/metrics`; the Caddyfile never exposes `/metrics`, `/docs`, `/openapi.json`.
- Security headers + strict CSP in `deploy/Caddyfile`; frontend renders LLM markdown with raw HTML escaped and `javascript:`/`data:` links blocked (covered by frontend tests).
- Regulatory content enters the knowledge base only after admin approval.

## Your responsibilities (not solvable in code)
- **HTTPS only.** Without TLS, passwords and tokens cross the LAN in clear text.
- **Firewall the model servers** (8081/8082) and PostgreSQL (5432): only the API host may reach them.
- **Internet search** (`WEB_SEARCH_ENABLED`) sends query text to DuckDuckGo/Wikipedia. The privacy gate is a keyword list; it cannot know your customer or product names. Keep it off for multi-company use.
- Rotate any key/password that was ever committed to a public repository; make repos private.
- Browser tokens: "remember me" stores the session token in `localStorage` (7-day default TTL is reduced to 24h in the production template).

## Added in this release
- `scripts/check_secrets.py` (run before every push), `.gitignore` covering `.env`, `data/`, `backups/`.
- `scripts/lock_ports.ps1`: default-deny inbound, only 80/443 open, llama/Postgres ports closed to the LAN.
- Services bind to 127.0.0.1; HTTPS via Caddy (`deploy/Caddyfile.host` or Docker `deploy/Caddyfile`).

## Known gaps / next steps
- No audit log of admin actions (user creation, regulatory approval) is written yet.
- scrypt cost is `n=2^14`; raise to `2^17` for new deployments if CPU allows.
- Login limiter keyed by username allows a deliberate lock-out of a known username (trade-off vs. brute force).
- No encryption at rest for `data/` (use disk encryption such as BitLocker/LUKS).
- API keys (`API_KEYS`) are plain text in `.env`; prefer login accounts.
