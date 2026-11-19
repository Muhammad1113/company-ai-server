"""
Audit log repositories: SQLite (hamesha), PostgreSQL ka Python logic (nakli asyncpg, tests/fake_pg.py) aur asli PostgreSQL
(sirf TEST_DATABASE_URL set ho to; alag schema test_audit).
"""
import asyncio
import os
import time

import pytest

from tests.fake_pg import FakeDb

DSN = os.environ.get("TEST_DATABASE_URL", "")
KINDS = ["sqlite", "fakepg", pytest.param("pg", marks=pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set"))]


async def _make(kind):
    from app.database.audit import AuditRepository
    from app.database.pg_audit import PgAuditRepository
    if kind == "sqlite":
        repo, db = AuditRepository(":memory:"), None
    elif kind == "fakepg":
        repo, db = PgAuditRepository(FakeDb()), None
    else:
        import asyncpg
        from app.database.postgres import PostgresDatabase
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute('DROP SCHEMA IF EXISTS "test_audit" CASCADE')
        finally:
            await conn.close()
        db = PostgresDatabase(DSN, schema="test_audit")
        await db.connect()
        repo = PgAuditRepository(db)
    assert not repo.ready
    await repo.init()
    assert repo.ready
    return repo, db


@pytest.mark.parametrize("kind", KINDS)
def test_record_list_filter_and_privacy(kind):
    async def run():
        repo, db = await _make(kind)
        try:
            await repo.record("root", "admin", None, "company.create", "acme", {"max_users": 5}, None, True)
            await repo.record("root", "admin", None, "user.create", "ali", {"role": "user", "company": "acme"}, None, True)
            await repo.record("root", "admin", None, "user.password_reset", "ali", {"fields": ["password"]}, None, True)
            await repo.record("acme_boss", "company_admin", "acme", "document.delete", "co_acme__docs/x", None, "10.0.0.5", True)
            await repo.record("mallory", None, None, "auth.login_failed", None, None, "10.0.0.9", False)

            everything = await repo.list(limit=50)
            assert [e["action"] for e in everything] == [
                "auth.login_failed", "document.delete", "user.password_reset", "user.create", "company.create"]     # naye pehle
            first = everything[0]
            assert first["ok"] is False and first["ip"] == "10.0.0.9" and first["actor"] == "mallory" and first["detail"] is None
            assert abs(first["ts"] - time.time()) < 60

            assert [e["action"] for e in await repo.list(action="user")] == ["user.password_reset", "user.create"]   # prefix
            assert [e["action"] for e in await repo.list(action="user.create")] == ["user.create"]
            assert await repo.list(action="us") == []                                                                  # partial word nahi
            assert await repo.list(action="%") == []                                                                   # LIKE injection nahi
            assert [e["actor"] for e in await repo.list(actor="MALLORY")] == ["mallory"]                               # case-insensitive
            assert [e["action"] for e in await repo.list(company="acme")] == ["document.delete"]
            assert [e["actor"] for e in await repo.list(ok=False)] == ["mallory"]
            assert len(await repo.list(ok=True)) == 4

            page1 = await repo.list(limit=2, offset=0)
            page2 = await repo.list(limit=2, offset=2)
            assert [e["id"] for e in page1 + page2] == [e["id"] for e in everything[:4]]

            detail = next(e for e in everything if e["action"] == "user.create")["detail"]
            assert detail == {"role": "user", "company": "acme"}
        finally:
            await repo.close()
            assert not repo.ready
            if db is not None:
                await db.close()
    asyncio.run(run())


@pytest.mark.parametrize("kind", KINDS)
def test_purge_removes_only_old_entries(kind):
    async def run():
        repo, db = await _make(kind)
        try:
            await repo.record("a", None, None, "auth.login", None, None, None, True)
            assert await repo.purge(365) == 0                  # taza entry nahi jati
            assert len(await repo.list()) == 1
            await asyncio.sleep(0.05)
            assert await repo.purge(0.00000001) == 1           # bohot chhoti retention => purani hat jati hai
            assert await repo.list() == []
        finally:
            await repo.close()
            if db is not None:
                await db.close()
    asyncio.run(run())


def test_helper_is_best_effort_and_silent_when_not_ready(monkeypatch):
    """audit.record kabhi exception nahi uchhalta, aur repo ready na ho to kuch nahi karta."""
    from app.core.security import ClientInfo
    from app.database.audit import AuditRepository
    from app.services import audit, container

    async def run():
        repo = AuditRepository(":memory:")                          # init nahi hui
        monkeypatch.setattr(container, "audit_repo", repo)
        await audit.record("x", "auth.login")                       # no-op, no error
        await repo.init()
        await audit.record(ClientInfo(name="root", role="admin"), "company.create", target="acme", detail={"k": 1})
        events = await repo.list()
        assert len(events) == 1 and events[0]["actor"] == "root" and events[0]["role"] == "admin"

        class Broken:
            ready = True

            async def record(self, *a, **k):
                raise RuntimeError("db down")
        monkeypatch.setattr(container, "audit_repo", Broken())
        await audit.record("x", "auth.login")                       # error nigal leta hai, request fail nahi hoti
        await repo.close()
    asyncio.run(run())
