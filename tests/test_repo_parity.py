"""
SQLite aur PostgreSQL repositories ek jaisa behave karti hain: ek hi test dono par chalta hai.
SQLite hamesha chalta hai; PostgreSQL wala sirf tab jab TEST_DATABASE_URL set ho (scripts\\setup_postgres.ps1).
PostgreSQL ke liye alag schema (test_parity) istemal hota hai, asli tables ko haath nahi lagta.
"""
import asyncio
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL", "")
KINDS = ["sqlite", pytest.param("pg", marks=pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set"))]


class Env:
    pass


async def make_env(kind: str, ttl_hours: float = 1.0) -> Env:
    from app.database.accounts import AccountsRepository
    from app.database.conversations import ConversationRepository
    from app.database.regulatory import RegulatoryRepository
    e = Env()
    e.db = None
    if kind == "sqlite":
        e.accounts = AccountsRepository(":memory:", session_ttl_hours=ttl_hours, scrypt_n=2 ** 10)
        e.convs = ConversationRepository(":memory:")
        e.reg = RegulatoryRepository(":memory:")
    else:
        import asyncpg
        from app.database.pg_accounts import PgAccountsRepository
        from app.database.pg_conversations import PgConversationRepository
        from app.database.pg_regulatory import PgRegulatoryRepository
        from app.database.postgres import PostgresDatabase
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute('DROP SCHEMA IF EXISTS "test_parity" CASCADE')
        finally:
            await conn.close()
        e.db = PostgresDatabase(DSN, schema="test_parity")
        await e.db.connect()
        e.accounts = PgAccountsRepository(e.db, session_ttl_hours=ttl_hours, scrypt_n=2 ** 10)
        e.convs = PgConversationRepository(e.db)
        e.reg = PgRegulatoryRepository(e.db)
    for r in (e.accounts, e.convs, e.reg):
        await r.init()
    return e


async def close_env(e: Env):
    for r in (e.accounts, e.convs, e.reg):
        await r.close()
    if e.db is not None:
        await e.db.close()


def scenario(fn):
    """Test function (kind) -> dono backends par env ke saath chalao."""
    @pytest.mark.parametrize("kind", KINDS)
    def wrapper(kind):
        async def run():
            e = await make_env(kind)
            try:
                await fn(e)
            finally:
                await close_env(e)
        asyncio.run(run())
    wrapper.__name__ = fn.__name__
    wrapper.scenario = fn
    return wrapper


async def expect_error(coro, text: str):
    try:
        await coro
    except ValueError as exc:
        assert text in str(exc), str(exc)
        return
    raise AssertionError(f"expected ValueError containing {text!r}")


# ------------------------------------------------------------------ accounts
@scenario
async def test_accounts_create_auth_and_duplicates(e):
    a = e.accounts
    await a.create_company("acme", "Acme Surgical", "info@acme.pk", 2)
    await expect_error(a.create_company("acme", "Again"), "exists")
    await expect_error(a.create_company("Bad Slug", "x"), "slug")
    assert not a.has_users
    u = await a.create_user("Alice", "correct-horse-1", "user", None, "acme")
    assert a.has_users and u["company"] == "acme" and u["collections"] is None
    assert (await a.authenticate("alice", "correct-horse-1"))["username"] == "Alice"      # case-insensitive
    assert await a.authenticate("alice", "wrong-password") is None
    assert await a.authenticate("nobody", "correct-horse-1") is None
    await expect_error(a.create_user("ALICE", "another-password", "user", None, "acme"), "exists")
    await expect_error(a.create_user("bob", "short", "user", None, "acme"), "password")
    await expect_error(a.create_user("bob", "correct-horse-1", "user", None, None), "company")
    await expect_error(a.create_user("bob", "correct-horse-1", "user", None, "nosuch"), "company")
    await a.create_user("bob", "correct-horse-1", "user", None, "acme")
    await expect_error(a.create_user("carl", "correct-horse-1", "user", None, "acme"), "limit")   # max_users=2
    root = await a.create_user("root", "correct-horse-1", "admin")
    assert root["company"] is None and root["role"] == "admin"
    await expect_error(a.create_user("root2", "correct-horse-1", "admin", None, "acme"), "super admin")


@scenario
async def test_accounts_sessions_disable_and_password(e):
    a = e.accounts
    await a.create_company("acme", "Acme")
    await a.create_company("beta", "Beta")
    u = await a.create_user("ali", "correct-horse-1", "user", None, "acme")
    z = await a.create_user("zed", "correct-horse-1", "user", None, "beta")
    tok, exp = await a.create_session(u["id"])
    assert exp > 0 and (await a.get_session_user(tok))["username"] == "ali"
    assert await a.get_session_user("garbage") is None
    assert [x["username"] for x in await a.list_online()] == ["ali"]
    assert {c["slug"]: c["online"] for c in await a.list_companies()} == {"acme": 1, "beta": 0}

    # company_admin sirf apni company ke users badal sakta hai
    assert await a.update_user("zed", disabled=True, only_company="acme") is False
    assert await a.update_user("ali", only_company="acme", disabled=False) is True
    await expect_error(a.update_user("ali", collections=["hr"]), "company users")

    # password change purane sessions band karta hai
    await expect_error(a.change_password("ali", "wrong", "new-password-1"), "incorrect")
    await a.change_password("ali", "correct-horse-1", "new-password-1")
    assert await a.get_session_user(tok) is None
    assert await a.authenticate("ali", "correct-horse-1") is None
    assert (await a.authenticate("ali", "new-password-1")) is not None

    # user disable => login band; company disable => sab sessions foran band
    tok2, _ = await a.create_session(u["id"])
    tz, _ = await a.create_session(z["id"])
    await a.update_user("ali", disabled=True)
    assert await a.get_session_user(tok2) is None and await a.authenticate("ali", "new-password-1") is None
    await a.update_company("beta", disabled=True)
    assert await a.get_session_user(tz) is None
    await a.update_company("beta", disabled=False, name="Beta Instruments", email="x@beta.pk", max_users=5)
    assert (await a.get_company("beta"))["name"] == "Beta Instruments"
    assert len(await a.list_users("acme")) == 1 and len(await a.list_users()) == 2
    await a.delete_session(tz)
    assert await a.get_session_user(tz) is None


@pytest.mark.parametrize("kind", KINDS)
def test_expired_session_is_rejected(kind):
    async def run():
        e = await make_env(kind, ttl_hours=-1)           # foran expire
        try:
            await e.accounts.create_company("acme", "Acme")
            u = await e.accounts.create_user("ali", "correct-horse-1", "user", None, "acme")
            tok, _ = await e.accounts.create_session(u["id"])
            assert await e.accounts.get_session_user(tok) is None
        finally:
            await close_env(e)
    asyncio.run(run())


# ------------------------------------------------------------------ conversations
@scenario
async def test_conversations_owner_isolation_and_messages(e):
    c = e.convs
    a1 = await c.create("alice", "First")
    a2 = await c.create("alice")
    b1 = await c.create("bob", "Bob chat")
    assert a2["title"] == "New chat"
    assert {x["id"] for x in await c.list_conversations("alice")} == {a1["id"], a2["id"]}
    assert await c.get(a1["id"], "bob") is None                          # doosre ki conversation nahi dikhti
    assert await c.rename(a1["id"], "bob", "hacked") is False
    assert await c.delete(a1["id"], "bob") is False
    assert (await c.get(a1["id"], "alice"))["title"] == "First"

    await c.add_message(a1["id"], "user", "hello")
    await c.add_message(a1["id"], "assistant", "hi", sources=[{"doc": "x", "page": 2}])
    msgs = await c.list_messages(a1["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["sources"] == [{"doc": "x", "page": 2}]
    assert len(await c.list_messages(a1["id"], limit=1)) == 1
    assert (await c.list_conversations("alice"))[0]["id"] == a1["id"]    # naya message => sab se upar

    await c.set_title_if_default(a2["id"], "Auto title")
    await c.set_title_if_default(a1["id"], "Should not change")
    assert (await c.get(a2["id"], "alice"))["title"] == "Auto title"
    assert (await c.get(a1["id"], "alice"))["title"] == "First"

    assert await c.delete_last_assistant(a1["id"]) is True
    assert await c.delete_last_assistant(a1["id"]) is False              # ab aakhri user ka hai
    assert await c.delete(a1["id"], "alice") is True
    assert await c.list_messages(a1["id"]) == []                         # messages bhi gaye
    assert (await c.get(b1["id"], "bob")) is not None


# ------------------------------------------------------------------ regulatory
def _ver(**kw):
    base = dict(collection="reg__eu", doc_key="eu-mdr", version="v1", status="active", archive_dir="reg__eu/eu-mdr/v1",
                filename="a.pdf", chars=10, chunks=2, uploaded_by="root")
    base.update(kw)
    return base


@scenario
async def test_regulatory_versions_pending_and_state(e):
    r = e.reg
    assert await r.count_pending() == 0
    v1 = await r.add_version(**_ver())
    await expect_error(r.add_version(**_ver()), "already exists")
    v2 = await r.add_version(**_ver(version="v2", status="archived"))
    assert (await r.get_active("reg__eu", "eu-mdr"))["id"] == v1
    assert (await r.get_version("reg__eu", "eu-mdr", "v2"))["id"] == v2
    await r.set_status(v1, "archived")
    await r.set_status(v2, "active")
    assert (await r.get_active("reg__eu", "eu-mdr"))["version"] == "v2"
    assert (await r.get_by_id(v1))["status"] == "archived" and await r.get_by_id(9999) is None
    assert len(await r.list_versions()) == 2 and len(await r.list_versions("reg__uk")) == 0

    await r.add_code("GAD", "Forceps")
    await r.add_code("GAD", "Forceps v2")
    assert [(c["code"], c["label"]) for c in await r.list_codes()] == [("GAD", "Forceps v2")]
    for p in ("878", "82", "820"):
        await r.add_part(p, None)
    assert [p["part"] for p in await r.list_parts()] == ["82", "820", "878"]      # numeric order
    assert await r.remove_part("82") and not await r.remove_part("82") and await r.remove_code("GAD")

    rec = {"key": "510k:K1", "kind": "510k", "code": "GAD", "hash": "h1", "data": {"a": 1}}
    await r.upsert_snapshot([rec])
    await r.upsert_snapshot([{**rec, "hash": "h2", "data": {"a": 2}}])
    snap = await r.snapshot()
    assert snap["510k:K1"]["hash"] == "h2" and snap["510k:K1"]["data"] == {"a": 2}
    await r.delete_snapshot(["510k:K1"])
    assert await r.snapshot() == {}
    await r.add_ignored([("k", "h"), ("k", "h")])
    assert await r.ignored() == {("k", "h")}

    p1 = await r.create_pending({"total": 1}, [rec])
    e1 = await r.create_pending({"total": 5}, [rec], source="ecfr")                 # alag source: supersede nahi
    p2 = await r.create_pending({"total": 2}, [rec, rec])                           # purana openfda superseded
    assert (await r.get_pending(p1))["status"] == "superseded"
    assert (await r.get_pending(e1))["status"] == "pending"
    assert await r.count_pending() == 2
    assert [x["id"] for x in await r.list_pending(source="openfda")] == [p2]
    assert {x["id"] for x in await r.list_pending(include_decided=True)} == {p1, e1, p2}
    got = await r.get_pending(p2)
    assert got["payload"] == [rec, rec] and got["summary"] == {"total": 2} and got["source"] == "openfda"
    assert await r.decide(p2, "approved", "root") is True
    assert await r.decide(p2, "rejected", "root") is False                          # double decide nahi
    assert (await r.get_pending(p2))["status"] == "approved"

    await r.set_state("last_run", {"changes": 3})
    assert await r.get_state("last_run") == {"changes": 3} and await r.get_state("nope", 7) == 7
    await r.set_state("last_run", {"changes": 4})
    assert (await r.get_state("last_run"))["changes"] == 4
