"""
Asli PostgreSQL + pgvector par HTTP-level company isolation (asli routes, TestClient, do companies, SAME doc_id).
Sirf TEST_DATABASE_URL set hone par chalta hai (scripts\\setup_postgres.ps1). Alag schema (test_isolation) istemal hota hai.
Accounts, sessions aur vectors sab PostgreSQL mein hote hain; LLM server ki zaroorat nahi.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("asyncpg")

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_PATH": str(Path(_tmp) / "t.db"), "DATABASE_URL": "", "API_KEYS": "",
    "RAG_STORE_BACKEND": "json", "RAG_STORE_PATH": str(Path(_tmp) / "rag.json"),
    "RAG_EMBEDDER": "hashing", "RATE_LIMIT_PER_MINUTE": "0", "REG_ARCHIVE_DIR": str(Path(_tmp) / "arch"),
    "LOGIN_BURST": "1000", "LOGIN_ATTEMPTS_PER_MINUTE": "1000", "AGENT_ENABLED": "false", "CACHE_BACKEND": "none",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

P = "/api/v1"
PW = "password123"


@pytest.fixture(scope="module")
def world():
    import asyncpg

    from app.core.ratelimit import TokenBucketLimiter
    from app.database.pg_accounts import PgAccountsRepository
    from app.database.pg_conversations import PgConversationRepository
    from app.database.pg_regulatory import PgRegulatoryRepository
    from app.database.postgres import PostgresDatabase, PostgresUsageRepository
    from app.main import app
    from app.rag.embeddings import HashingEmbedder
    from app.rag.pgvector_store import PgVectorStore
    from app.rag.service import RagService
    from app.services import container

    async def drop():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute('DROP SCHEMA IF EXISTS "test_isolation" CASCADE')
        finally:
            await conn.close()
    asyncio.run(drop())

    mp = pytest.MonkeyPatch()
    db = PostgresDatabase(DSN, schema="test_isolation")
    embedder = HashingEmbedder(256)
    accounts = PgAccountsRepository(db, scrypt_n=2 ** 10)
    mp.setattr(container, "db", db)
    mp.setattr(container, "usage_repo", PostgresUsageRepository(db))
    mp.setattr(container, "accounts_repo", accounts)
    mp.setattr(container, "conversations_repo", PgConversationRepository(db))
    mp.setattr(container, "regulatory_repo", PgRegulatoryRepository(db))
    mp.setattr(container.regulatory_service, "repo", container.regulatory_repo)
    mp.setattr(container, "rag_service", RagService(embedder, PgVectorStore(db, embedder.id, 256), None))
    mp.setattr(container, "api_keys", [])
    mp.setattr(container, "rate_limiter", None)
    mp.setattr(container, "login_limiter", TokenBucketLimiter(6000, 1000))

    # `with` => TestClient ek hi event loop chalata hai; lifespan db.connect + sab init usi loop mein karta hai
    with TestClient(app) as c:
        call = c.portal.call

        async def seed():
            await accounts.create_company("acme", "Acme Surgical")
            await accounts.create_company("beta", "Beta Instruments")
            for name, role, comp in [("root", "admin", None), ("acme_boss", "company_admin", "acme"),
                                     ("acme_ali", "user", "acme"), ("beta_boss", "company_admin", "beta"),
                                     ("beta_bilal", "user", "beta")]:
                await accounts.create_user(name, PW, role, None, comp)
        call(seed)

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

        yield c, h, ingest
    mp.undo()


def _search(c, h, who, collection, q="price list confidential"):
    return c.post(f"{P}/rag/search", headers=h(who), json={"question": q, "collection": collection})


def test_same_doc_id_in_two_companies_never_mixes(world):
    c, h, ingest = world
    assert ingest("acme_boss", "co_acme__docs", "price_list", "ACMESECRET forceps price list confidential").status_code == 200
    assert ingest("beta_boss", "co_beta__docs", "price_list", "BETASECRET scissors price list confidential").status_code == 200
    assert ingest("root", "reg__fda", "fda_rule", "FDA class one device rule for forceps").status_code == 200

    a = _search(c, h, "acme_ali", "co_acme__docs").json()["hits"]
    b = _search(c, h, "beta_bilal", "co_beta__docs").json()["hits"]
    assert a and all("ACMESECRET" in x["text"] and "BETASECRET" not in x["text"] for x in a)
    assert b and all("BETASECRET" in x["text"] and "ACMESECRET" not in x["text"] for x in b)

    assert _search(c, h, "acme_ali", "co_beta__docs").status_code == 403
    assert _search(c, h, "beta_bilal", "co_acme__docs").status_code == 403
    assert _search(c, h, "acme_ali", "reg__fda").status_code == 200           # shared regulatory sab parh sakte hain
    names = {x["name"] for x in c.get(f"{P}/rag/collections", headers=h("acme_ali")).json()["collections"]}
    assert "co_beta__docs" not in names


def test_delete_in_one_company_leaves_other_untouched(world):
    c, h, _ = world
    assert c.delete(f"{P}/rag/documents/price_list", params={"collection": "co_beta__docs"},
                    headers=h("acme_boss")).status_code == 403
    assert c.delete(f"{P}/rag/documents/price_list", params={"collection": "co_acme__docs"},
                    headers=h("acme_boss")).status_code == 200
    assert not _search(c, h, "acme_ali", "co_acme__docs").json()["hits"]
    assert any("BETASECRET" in x["text"] for x in _search(c, h, "beta_bilal", "co_beta__docs").json()["hits"])


def test_sessions_and_company_disable_on_postgres(world):
    c, h, _ = world
    assert c.get(f"{P}/auth/me", headers=h("beta_bilal")).status_code == 200
    assert c.patch(f"{P}/admin/companies/beta", headers=h("root"), json={"disabled": True}).status_code == 200
    assert _search(c, h, "beta_bilal", "co_beta__docs").status_code == 401
    assert c.post(f"{P}/auth/login", json={"username": "beta_bilal", "password": PW}).status_code == 401
    c.patch(f"{P}/admin/companies/beta", headers=h("root"), json={"disabled": False})
    assert c.post(f"{P}/auth/login", json={"username": "beta_bilal", "password": PW}).status_code == 200


def test_conversations_are_private_per_user_on_postgres(world):
    c, h, _ = world
    cid = c.post(f"{P}/conversations", headers=h("acme_ali"), json={}).json()["id"]
    assert c.get(f"{P}/conversations/{cid}", headers=h("beta_bilal")).status_code == 404
    assert c.get(f"{P}/conversations/{cid}", headers=h("acme_ali")).status_code == 200
