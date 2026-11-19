"""APP_ENV=production: auth fail-closed, METRICS_TOKEN, preflight checks."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_PATH": str(Path(_tmp) / "p.db"), "DATABASE_URL": "", "API_KEYS": "",
    "RAG_STORE_BACKEND": "json", "RAG_STORE_PATH": str(Path(_tmp) / "rag.json"),
    "RAG_EMBEDDER": "hashing", "RATE_LIMIT_PER_MINUTE": "0",
    "LOGIN_BURST": "1000", "LOGIN_ATTEMPTS_PER_MINUTE": "1000",
    "AGENT_ENABLED": "false", "CACHE_BACKEND": "none",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.production import production_issues  # noqa: E402
from app.main import app  # noqa: E402
from app.services import container  # noqa: E402

P = "/api/v1"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def world():
    from app.core.ratelimit import TokenBucketLimiter
    from app.database.accounts import AccountsRepository
    from app.database.usage import UsageRepository

    mp = pytest.MonkeyPatch()
    repo = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    usage = UsageRepository(":memory:")
    run(repo.init())
    run(usage.init())
    mp.setattr(container, "accounts_repo", repo)
    mp.setattr(container, "usage_repo", usage)
    mp.setattr(container, "api_keys", [])
    mp.setattr(container, "rate_limiter", None)
    mp.setattr(container, "login_limiter", TokenBucketLimiter(6000, 1000))
    yield repo, mp
    mp.undo()


def test_production_without_users_denies_everything(world):
    repo, mp = world
    mp.setattr(settings, "APP_ENV", "production")
    c = TestClient(app)
    assert c.get(f"{P}/usage/me").status_code == 401
    assert c.get(f"{P}/rag/collections").status_code == 401


def test_development_without_users_stays_open(world):
    repo, mp = world
    mp.setattr(settings, "APP_ENV", "development")
    c = TestClient(app)
    assert c.get(f"{P}/usage/me").status_code != 401


def test_production_user_created_by_script_works_without_restart(world):
    repo, mp = world
    mp.setattr(settings, "APP_ENV", "production")
    run(repo.create_company("acme", "Acme"))
    run(repo.create_user("ali", "password123", "user", None, "acme"))
    repo.has_users = False            # server ne user ko startup ke baad dekha (script se bana)
    c = TestClient(app)
    r = c.post(f"{P}/auth/login", json={"username": "ali", "password": "password123"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert c.get(f"{P}/auth/me", headers=h).status_code == 200
    assert c.get(f"{P}/auth/me").status_code == 401


def test_metrics_token(world):
    repo, mp = world
    c = TestClient(app)
    mp.setattr(settings, "METRICS_TOKEN", "")
    assert c.get("/metrics").status_code == 200
    mp.setattr(settings, "METRICS_TOKEN", "secret-metrics-token")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer secret-metrics-token"}).status_code == 200


def _cfg(**kw):
    base = dict(APP_ENV="production", METRICS_TOKEN="t", WEB_SEARCH_ENABLED=False, RAG_EMBEDDER="llama_cpp",
                RAG_STORE_BACKEND="pgvector", DATABASE_URL="postgresql://x", CORS_ORIGINS="",
                LATIN_ONLY_OUTPUT=False, SESSION_TTL_HOURS=24, LLAMA_BASE_URL="http://127.0.0.1:8081")
    base.update(kw)
    return SimpleNamespace(**base)


def test_preflight_clean_config_has_no_issues():
    assert production_issues(_cfg()) == []


def test_preflight_flags_dangerous_settings():
    levels = dict((m.split(":")[0].split(" ")[0], l) for l, m in production_issues(
        _cfg(WEB_SEARCH_ENABLED=True, RAG_EMBEDDER="hashing", CORS_ORIGINS="*", APP_ENV="development")))
    assert levels["WEB_SEARCH_ENABLED=true"] == "error"
    assert levels["RAG_EMBEDDER=hashing"] == "error"
    assert levels["CORS_ORIGINS"] == "error"
    assert levels["APP_ENV"] == "warn"
