"""
Company isolation tests. Chalane ka tareeqa (backend folder se):   python -m pytest tests -q
Har security change ke baad yeh pass hone chahiye.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import tenancy                                   # noqa: E402
from app.core.security import ClientInfo, parse_api_keys        # noqa: E402
from app.database.accounts import AccountsRepository            # noqa: E402


def run(coro):
    return asyncio.run(coro)


def client(user: dict) -> ClientInfo:
    """deps.get_client jaisa mapping."""
    return ClientInfo(name=user["username"], role=user["role"], company=user["company"],
                      collections=frozenset(user["collections"]) if user["collections"] else None)


@pytest.fixture()
def repo():
    r = AccountsRepository(":memory:", scrypt_n=2 ** 4)
    run(r.init())
    run(r.create_company("acme", "Acme Surgical", "a@acme.pk"))
    run(r.create_company("beta", "Beta Instruments", "b@beta.pk"))
    yield r
    run(r.close())


def mk(repo, name, role, company):
    return client(run(repo.create_user(name, "password123", role, None, company)))


# ---------------- pure policy ----------------
def test_slug_rules():
    assert tenancy.valid_slug("acme") and tenancy.valid_slug("sialkot_med")
    for bad in ("a", "Acme", "a__b", "_a", "a_", "ac me", "a/b", "x" * 40, "", None):
        assert not tenancy.valid_slug(bad), bad


def test_owner_of_cannot_be_spoofed():
    assert tenancy.owner_of("co_acme__docs") == "acme"
    assert tenancy.owner_of("co_acme_x__docs") == "acme_x"       # alag company, acme nahi
    assert tenancy.owner_of("co_acme__") is None
    assert tenancy.owner_of("reg__fda") is None
    assert tenancy.owner_of("default") is None


def test_company_user_isolation(repo):
    a, b = mk(repo, "ali", "user", "acme"), mk(repo, "bilal", "user", "beta")
    assert a.can_access("co_acme__docs") and not a.can_access("co_beta__docs")
    assert b.can_access("co_beta__docs") and not b.can_access("co_acme__docs")
    assert a.can_access("reg__fda") and a.can_access("reg__eu")
    # prefix tricks
    assert not a.can_access("co_acme_x__docs")
    assert not a.can_access("CO_acme__docs")
    assert not a.can_access("co_acme__docs/../co_beta__docs")
    assert not a.can_access("default") and not a.can_access("")


def test_company_user_cannot_write_anything(repo):
    a = mk(repo, "ali", "user", "acme")
    assert not a.can_manage("co_acme__docs") and not a.can_manage("reg__fda")


def test_company_admin_scope(repo):
    ca = mk(repo, "boss", "company_admin", "acme")
    assert ca.can_manage("co_acme__docs") and ca.can_manage("co_acme__manuals")
    assert not ca.can_manage("co_beta__docs")
    assert not ca.can_manage("reg__fda")           # shared sirf super admin badalta hai
    assert not ca.can_manage("default")


def test_super_admin_everything(repo):
    s = client(run(repo.create_user("root", "password123", "admin")))
    for c in ("co_acme__docs", "co_beta__docs", "reg__fda", "legacy"):
        assert s.can_access(c) and s.can_manage(c)


def test_deny_by_default_no_company():
    # purana "collections=None => sab" ab band
    assert not ClientInfo("legacy").can_access("anything")
    assert not ClientInfo("legacy", collections=frozenset()).can_access("hr")
    assert ClientInfo("legacy", collections=frozenset({"hr"})).can_access("hr")
    assert not ClientInfo("legacy", collections=frozenset({"hr"})).can_access("finance")
    assert not ClientInfo("legacy").can_manage("hr")


def test_explicit_list_ignored_for_company_users():
    # galti se list mein doosri company ka naam aa jaye to bhi access nahi
    c = ClientInfo("x", "user", frozenset({"co_beta__docs"}), company="acme")
    assert not c.can_access("co_beta__docs")


def test_api_keys_default_deny():
    (key, info), = parse_api_keys("svc:abcdefghijklmnop:user")
    assert not info.can_access("co_acme__docs")
    (key, info), = parse_api_keys("svc:abcdefghijklmnop:user:*")
    assert not info.can_access("co_acme__docs")
    with pytest.raises(ValueError):
        parse_api_keys("svc:abcdefghijklmnop:company_admin")


# ---------------- accounts / companies ----------------
def test_user_requires_company_rules(repo):
    with pytest.raises(ValueError):
        run(repo.create_user("nocomp", "password123", "user"))
    with pytest.raises(ValueError):
        run(repo.create_user("ghost", "password123", "user", None, "nope"))
    with pytest.raises(ValueError):
        run(repo.create_user("root", "password123", "admin", None, "acme"))


def test_company_users_get_no_manual_collections(repo):
    u = run(repo.create_user("ali", "password123", "user", ["co_beta__docs"], "acme"))
    assert u["collections"] is None


def test_company_scoped_user_management(repo):
    mk(repo, "ali", "user", "acme")
    mk(repo, "bilal", "user", "beta")
    assert [u["username"] for u in run(repo.list_users("acme"))] == ["ali"]
    # acme ka admin beta ke user ko chhoo nahi sakta (not found jaisa)
    assert run(repo.update_user("bilal", disabled=True, only_company="acme")) is False
    assert run(repo.update_user("ali", disabled=True, only_company="acme")) is True


def test_company_disable_blocks_login_and_sessions(repo):
    u = run(repo.create_user("ali", "password123", "user", None, "acme"))
    tok, _ = run(repo.create_session(u["id"]))
    assert run(repo.get_session_user(tok))["company"] == "acme"
    assert run(repo.authenticate("ali", "password123")) is not None

    run(repo.update_company("acme", disabled=True))
    assert run(repo.get_session_user(tok)) is None
    assert run(repo.authenticate("ali", "password123")) is None

    run(repo.update_company("acme", disabled=False))
    assert run(repo.authenticate("ali", "password123")) is not None


def test_max_users_limit(repo):
    run(repo.update_company("acme", max_users=1))
    run(repo.create_user("user_one", "password123", "user", None, "acme"))
    with pytest.raises(ValueError):
        run(repo.create_user("user_two", "password123", "user", None, "acme"))


def test_overview_counts_online(repo):
    u = run(repo.create_user("ali", "password123", "user", None, "acme"))
    run(repo.create_session(u["id"]))
    rows = {c["slug"]: c for c in run(repo.list_companies())}
    assert rows["acme"]["users"] == 1 and rows["acme"]["online"] == 1
    assert rows["beta"]["users"] == 0 and rows["beta"]["online"] == 0
    assert rows["acme"]["collection"] == "co_acme__docs"


def test_role_cannot_cross_admin_boundary(repo):
    mk(repo, "ali", "user", "acme")
    with pytest.raises(ValueError):
        run(repo.update_user("ali", role="admin"))


def test_legacy_db_migration(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', collections TEXT,
        disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
      CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL,
        expires_at REAL NOT NULL, last_used REAL NOT NULL);
    """)
    from app.database.accounts import hash_password
    c.execute("INSERT INTO users (username,password_hash,role,collections,created_at) VALUES (?,?,?,?,0)",
              ("old_admin", hash_password("password123", n=16), "admin", None))
    c.execute("INSERT INTO users (username,password_hash,role,collections,created_at) VALUES (?,?,?,?,0)",
              ("old_hr", hash_password("password123", n=16), "user", "hr"))
    c.commit(); c.close()

    r = AccountsRepository(path, scrypt_n=16)
    run(r.init())
    users = {u["username"]: u for u in run(r.list_users())}
    assert users["old_admin"]["role"] == "admin" and users["old_admin"]["company"] is None
    assert users["old_hr"]["company"] is None and users["old_hr"]["collections"] == ["hr"]
    assert run(r.authenticate("old_admin", "password123")) is not None
    run(r.close())


def test_list_online_and_usage_by_client(repo):
    from app.database.usage import UsageRepository
    u = run(repo.create_user("ali", "password123", "user", None, "acme"))
    run(repo.create_user("bilal", "password123", "user", None, "beta"))      # session nahi => offline
    run(repo.create_session(u["id"]))
    online = run(repo.list_online())
    assert [(o["username"], o["company"]) for o in online] == [("ali", "acme")]

    async def go():
        us = UsageRepository(":memory:")
        await us.init()
        await us.record(client="ali", endpoint="completions", status="success", latency_ms=100, prompt_tokens=5)
        await us.record(client="ali", endpoint="completions", status="error", latency_ms=300)
        return await us.by_client(24)
    rows = run(go())
    assert rows[0]["client"] == "ali" and rows[0]["requests"] == 2 and rows[0]["errors"] == 1
    assert rows[0]["avg_latency_ms"] == 200.0 and rows[0]["prompt_tokens"] == 5
    assert set(rows[0]) == {"client", "requests", "errors", "avg_latency_ms", "prompt_tokens", "completion_tokens", "last_ts"}
