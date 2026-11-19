"""
Production preflight: settings ki khatarnaak / kamzor combinations ki list.
Pure module (sirf settings object parhta hai) => seedha test hota hai.
Startup mein warning ke taur par aur `python scripts/preflight.py` mein (exit code 1 = problems).
"""
from __future__ import annotations


def production_issues(s) -> list[tuple[str, str]]:
    """[(level, message)]  level: 'error' (production mein nahi chalna chahiye) | 'warn'."""
    out: list[tuple[str, str]] = []
    add = out.append
    env = str(s.APP_ENV).strip().lower()

    if env != "production":
        add(("warn", "APP_ENV is not 'production': auth is open when no users/API keys exist."))
    if not s.METRICS_TOKEN:
        add(("warn", "METRICS_TOKEN empty: /metrics is open. Set a token and block /metrics at the proxy."))
    if s.WEB_SEARCH_ENABLED:
        add(("error", "WEB_SEARCH_ENABLED=true: company questions may be sent to external search engines. "
                      "Disable it for multi-company use, or review WEB_SEARCH_PRIVACY_TERMS."))
    if str(s.RAG_EMBEDDER).lower() == "hashing":
        add(("error", "RAG_EMBEDDER=hashing is a test embedder with weak retrieval. Use llama_cpp."))
    if str(s.RAG_STORE_BACKEND).lower() != "pgvector":
        add(("warn", "RAG_STORE_BACKEND is not pgvector: JSON store is for small pilots (<~5k chunks)."))
    if not s.DATABASE_URL:
        add(("warn", "DATABASE_URL empty: usage log uses SQLite."))
    if "*" in (s.CORS_ORIGINS or ""):
        add(("error", "CORS_ORIGINS contains '*'. Use same-origin proxy or explicit origins."))
    if getattr(s, "LATIN_ONLY_OUTPUT", False):
        add(("warn", "LATIN_ONLY_OUTPUT=true blocks Urdu/Arabic script in answers (intended for the small model)."))
    if s.SESSION_TTL_HOURS > 24 * 14:
        add(("warn", "SESSION_TTL_HOURS is longer than 14 days."))
    if s.LLAMA_BASE_URL.startswith("http://") and not any(
        h in s.LLAMA_BASE_URL for h in ("127.0.0.1", "localhost", "host.docker.internal", "llama")
    ):
        add(("warn", "LLAMA_BASE_URL is plain http to a non-local host: keep it on a private network."))
    return out
