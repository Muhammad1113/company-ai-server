"""
HTTP-level isolation: asli routes, do companies. Chalao:  python -m pytest tests -q
(fastapi / httpx install hona chahiye; LLM server ki zaroorat nahi, sirf retrieval + permissions test hote hain.)
"""
import asyncio
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


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def world():
    # Apni alag in-memory repos: asli data/ file ya doosre tests ki state se koi taalluq nahi
    from app.database.accounts import AccountsRepository
    from app.database.usage import UsageRepository
    from app.rag.embeddings import HashingEmbedder
    from app.rag.service import RagService
    from app.rag.store import JsonVectorStore

    mp = pytest.MonkeyPatch()
    repo = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    usage = UsageRepository(":memory:")
    embedder = HashingEmbedder(256)
    mp.setattr(container, "accounts_repo", repo)
    mp.setattr(container, "usage_repo", usage)
    mp.setattr(container, "rag_service", RagService(embedder, JsonVectorStore(None, embedder.id), None))
    mp.setattr(container, "api_keys", [])
    mp.setattr(container, "rate_limiter", None)
    mp.setattr(container, "login_limiter", __import__("app.core.ratelimit", fromlist=["x"]).TokenBucketLimiter(6000, 1000))
    run(repo.init())
    run(usage.init())
    run(repo.create_company("acme", "Acme Surgical"))
    run(repo.create_company("beta", "Beta Instruments"))
    for name, role, comp in [("root", "admin", None),
                             ("acme_boss", "company_admin", "acme"), ("acme_ali", "user", "acme"),
                             ("beta_boss", "company_admin", "beta"), ("beta_bilal", "user", "beta")]:
        run(repo.create_user(name, PW, role, None, comp))

    c = TestClient(app)
    tokens = {}
    for name in ("root", "acme_boss", "acme_ali", "beta_boss", "beta_bilal"):
        r = c.post(f"{P}/auth/login", json={"username": name, "password": PW})
        assert r.status_code == 200, r.text
        tokens[name] = r.json()["token"]

    def h(name):
        return {"Authorization": f"Bearer {tokens[name]}"}

    def ingest(who, collection, doc_id, text):
        return c.post(f"{P}/rag/ingest", headers=h(who),
                      json={"doc_id": doc_id, "text": text, "collection": collection})

    assert ingest("root", "co_acme__docs", "acme_secret", "ACMESECRET forceps price list confidential").status_code == 200
    assert ingest("root", "co_beta__docs", "beta_secret", "BETASECRET scissors price list confidential").status_code == 200
    assert ingest("root", "reg__fda", "fda_rule", "FDA class one device rule for forceps").status_code == 200
    yield c, h, ingest
    mp.undo()


def search(c, h, who, collection, q="forceps price list confidential"):
    return c.post(f"{P}/rag/search", headers=h(who), json={"question": q, "collection": collection})


def test_user_reads_own_and_shared_only(world):
    c, h, _ = world
    r = search(c, h, "acme_ali", "co_acme__docs")
    assert r.status_code == 200 and any(x["doc_id"] == "acme_secret" for x in r.json()["hits"])
    assert search(c, h, "acme_ali", "reg__fda").status_code == 200
    assert search(c, h, "acme_ali", "co_beta__docs").status_code == 403
    assert search(c, h, "beta_bilal", "co_acme__docs").status_code == 403
    assert search(c, h, "acme_ali", "default").status_code == 403


def test_query_endpoint_also_blocked(world):
    c, h, _ = world
    r = c.post(f"{P}/rag/query", headers=h("acme_ali"), json={"question": "price", "collection": "co_beta__docs"})
    assert r.status_code == 403


def test_collections_list_hides_other_company(world):
    c, h, _ = world
    names = {x["name"] for x in c.get(f"{P}/rag/collections", headers=h("acme_ali")).json()["collections"]}
    assert "co_acme__docs" in names and "reg__fda" in names and "co_beta__docs" not in names


def test_plain_user_cannot_write_or_list(world):
    c, h, ingest = world
    assert ingest("acme_ali", "co_acme__docs", "x", "hello").status_code == 403
    assert c.get(f"{P}/rag/documents", params={"collection": "co_acme__docs"}, headers=h("acme_ali")).status_code == 403
    assert c.delete(f"{P}/rag/documents/acme_secret", params={"collection": "co_acme__docs"},
                    headers=h("acme_ali")).status_code == 403


def test_company_admin_scope(world):
    c, h, ingest = world
    assert ingest("acme_boss", "co_acme__docs", "manual", "acme own manual text").status_code == 200
    assert ingest("acme_boss", "co_beta__docs", "evil", "overwrite").status_code == 403
    assert ingest("acme_boss", "reg__fda", "evil", "fake fda rule").status_code == 403
    assert c.get(f"{P}/rag/documents", params={"collection": "co_beta__docs"}, headers=h("acme_boss")).status_code == 403
    assert c.delete(f"{P}/rag/documents/beta_secret", params={"collection": "co_beta__docs"},
                    headers=h("acme_boss")).status_code == 403
    r = c.post(f"{P}/rag/ingest-file", params={"filename": "a.txt", "collection": "co_beta__docs"},
               headers=h("acme_boss"), content=b"hello world")
    assert r.status_code == 403
    assert c.delete(f"{P}/rag/documents/manual", params={"collection": "co_acme__docs"},
                    headers=h("acme_boss")).status_code == 200
    # beta ka document salamat
    assert any(x["doc_id"] == "beta_secret" for x in search(c, h, "beta_bilal", "co_beta__docs").json()["hits"])


def test_admin_routes_super_admin_only(world):
    c, h, _ = world
    for who in ("acme_boss", "acme_ali"):
        assert c.get(f"{P}/admin/companies", headers=h(who)).status_code == 403
        assert c.get(f"{P}/admin/users", headers=h(who)).status_code == 403
        assert c.get(f"{P}/rag/stats", headers=h(who)).status_code == 403
    assert c.get(f"{P}/admin/companies", headers=h("root")).status_code == 200


def test_company_admin_manages_only_own_users(world):
    c, h, _ = world
    users = {u["username"] for u in c.get(f"{P}/company", headers=h("acme_boss")).json()["users"]}
    assert users == {"acme_boss", "acme_ali"}
    assert c.patch(f"{P}/company/users/beta_bilal", headers=h("acme_boss"), json={"disabled": True}).status_code == 404
    assert c.get(f"{P}/company", headers=h("acme_ali")).status_code == 403
    r = c.post(f"{P}/company/users", headers=h("acme_boss"), json={"username": "acme_new", "password": PW})
    assert r.status_code == 201 and r.json()["company"] == "acme" and r.json()["role"] == "user"


def test_company_disable_kills_access_immediately(world):
    c, h, _ = world
    assert search(c, h, "beta_bilal", "co_beta__docs").status_code == 200
    assert c.patch(f"{P}/admin/companies/beta", headers=h("root"), json={"disabled": True}).status_code == 200
    assert search(c, h, "beta_bilal", "co_beta__docs").status_code == 401
    assert c.post(f"{P}/auth/login", json={"username": "beta_bilal", "password": PW}).status_code == 401
    c.patch(f"{P}/admin/companies/beta", headers=h("root"), json={"disabled": False})


def test_overview_super_admin_only_and_no_content(world):
    c, h, _ = world
    assert c.get(f"{P}/admin/overview", headers=h("acme_boss")).status_code == 403
    assert c.get(f"{P}/admin/overview", headers=h("acme_ali")).status_code == 403
    r = c.get(f"{P}/admin/overview", headers=h("root"))
    assert r.status_code == 200
    body = r.json()
    assert {x["slug"] for x in body["companies"]} >= {"acme", "beta"}
    assert {"active", "max_concurrent", "waiting", "max_queue_size"} <= set(body["queue"])
    assert "ACMESECRET" not in r.text and "BETASECRET" not in r.text      # documents/chats ka content nahi
