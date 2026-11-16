"""HTTP-level audit log: asli routes. Super admin hi parh sakta hai; password kabhi record nahi hota."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_PATH": str(Path(_tmp) / "t.db"), "DATABASE_URL": "", "API_KEYS": "",
    "RAG_STORE_BACKEND": "json", "RAG_STORE_PATH": str(Path(_tmp) / "rag.json"),
    "RAG_EMBEDDER": "hashing", "RATE_LIMIT_PER_MINUTE": "0",
    "LOGIN_BURST": "1000", "LOGIN_ATTEMPTS_PER_MINUTE": "1000",
    "AGENT_ENABLED": "false", "CACHE_BACKEND": "none",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app            # noqa: E402
from app.services import container  # noqa: E402

P = "/api/v1"
PW = "password123"
SECRET_PW = "Sup3r-Secret-Pw-9"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def world():
    from app.core.ratelimit import TokenBucketLimiter
    from app.database.accounts import AccountsRepository
    from app.database.audit import AuditRepository
    from app.database.usage import UsageRepository
    from app.rag.embeddings import HashingEmbedder
    from app.rag.service import RagService
    from app.rag.store import JsonVectorStore

    mp = pytest.MonkeyPatch()
    repo = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    usage = UsageRepository(":memory:")
    audit_repo = AuditRepository(":memory:")
    embedder = HashingEmbedder(256)
    mp.setattr(container, "accounts_repo", repo)
    mp.setattr(container, "usage_repo", usage)
    mp.setattr(container, "audit_repo", audit_repo)
    mp.setattr(container, "rag_service", RagService(embedder, JsonVectorStore(None, embedder.id), None))
    mp.setattr(container, "api_keys", [])
    mp.setattr(container, "rate_limiter", None)
    mp.setattr(container, "login_limiter", TokenBucketLimiter(6000, 1000))
    run(repo.init())
    run(usage.init())
    run(audit_repo.init())
    run(repo.create_company("acme", "Acme Surgical"))
    for name, role, comp in [("root", "admin", None), ("acme_boss", "company_admin", "acme"), ("acme_ali", "user", "acme")]:
        run(repo.create_user(name, PW, role, None, comp))

    c = TestClient(app)
    tokens = {}
    for name in ("root", "acme_boss", "acme_ali"):
        r = c.post(f"{P}/auth/login", json={"username": name, "password": PW})
        assert r.status_code == 200, r.text
        tokens[name] = r.json()["token"]

    def h(name):
        return {"Authorization": f"Bearer {tokens[name]}"}

    yield c, h
    mp.undo()


def _events(c, h, **params):
    r = c.get(f"{P}/admin/audit", headers=h("root"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_only_super_admin_can_read_the_audit_log(world):
    c, h = world
    assert c.get(f"{P}/admin/audit", headers=h("acme_boss")).status_code == 403
    assert c.get(f"{P}/admin/audit", headers=h("acme_ali")).status_code == 403
    assert c.get(f"{P}/admin/audit").status_code in (401, 403)


def test_admin_actions_are_recorded_without_secrets(world):
    c, h = world
    assert c.post(f"{P}/admin/companies", headers=h("root"), json={"slug": "beta", "name": "Beta"}).status_code == 201
    assert c.post(f"{P}/admin/users", headers=h("root"),
                  json={"username": "beta_boss", "password": SECRET_PW, "role": "company_admin", "company": "beta"}).status_code == 201
    assert c.patch(f"{P}/admin/users/beta_boss", headers=h("root"), json={"password": SECRET_PW + "x"}).status_code == 200
    assert c.patch(f"{P}/admin/companies/beta", headers=h("root"), json={"disabled": True}).status_code == 200

    body = _events(c, h)
    actions = [e["action"] for e in body["events"]]
    for expected in ("company.create", "user.create", "user.password_reset", "company.disable", "auth.login"):
        assert expected in actions, actions
    assert SECRET_PW not in json.dumps(body)                         # password kisi bhi shakal mein nahi
    pr = next(e for e in body["events"] if e["action"] == "user.password_reset")
    assert pr["actor"] == "root" and pr["role"] == "admin" and pr["target"] == "beta_boss" and pr["detail"] == {"fields": ["password"]}


def test_company_admin_and_document_actions_carry_company(world):
    c, h = world
    assert c.post(f"{P}/company/users", headers=h("acme_boss"), json={"username": "acme_zoya", "password": PW}).status_code == 201
    assert c.post(f"{P}/rag/ingest", headers=h("acme_boss"),
                  json={"doc_id": "price", "text": "forceps price list", "collection": "co_acme__docs"}).status_code == 200
    assert c.delete(f"{P}/rag/documents/price", params={"collection": "co_acme__docs"}, headers=h("acme_boss")).status_code == 200
    events = _events(c, h, company="acme")["events"]
    by_action = {e["action"]: e for e in events}
    assert by_action["user.create"]["actor"] == "acme_boss" and by_action["user.create"]["target"] == "acme_zoya"
    assert by_action["document.ingest"]["target"] == "co_acme__docs/price"
    assert by_action["document.delete"]["detail"]["chunks_removed"] >= 1
    assert "forceps" not in json.dumps(events)                       # document ka text kabhi nahi


def test_failed_login_is_recorded_and_filters_work(world):
    c, h = world
    assert c.post(f"{P}/auth/login", json={"username": "acme_ali", "password": "wrong-password"}).status_code == 401
    failed = _events(c, h, ok="false")["events"]
    assert failed and failed[0]["action"] == "auth.login_failed" and failed[0]["actor"] == "acme_ali" and failed[0]["ok"] is False
    assert all(e["action"].startswith("user.") for e in _events(c, h, action="user")["events"])
    page = _events(c, h, limit=2)
    assert len(page["events"]) == 2 and page["has_more"] is True
    assert c.get(f"{P}/admin/audit", headers=h("root"), params={"limit": 0}).status_code == 422
