"""
Postgres repositories ka PYTHON logic bina Postgres ke (SQLite-backed asyncpg nakal, tests/fake_pg.py).
Wahi scenarios jo SQLite aur asli PostgreSQL par chalte hain (tests/test_repo_parity.py). Asli SQL dialect ka saboot
asli PostgreSQL par chalne wale tests hain: scripts/setup_postgres.ps1.
"""
import asyncio

from tests.fake_pg import FakeDb
from tests import test_repo_parity as parity


async def _pg_env(ttl_hours=1.0):
    from app.database.pg_accounts import PgAccountsRepository
    from app.database.pg_conversations import PgConversationRepository
    from app.database.pg_regulatory import PgRegulatoryRepository
    e = parity.Env()
    e.db = FakeDb()
    e.accounts = PgAccountsRepository(e.db, session_ttl_hours=ttl_hours, scrypt_n=2 ** 10)
    e.convs = PgConversationRepository(e.db)
    e.reg = PgRegulatoryRepository(e.db)
    for r in (e.accounts, e.convs, e.reg):
        await r.init()
    return e


def _run(scenario_fn, ttl=1.0):
    async def go():
        await scenario_fn(await _pg_env(ttl))
    asyncio.run(go())


def test_accounts_create_auth_and_duplicates():
    _run(parity.test_accounts_create_auth_and_duplicates.scenario)


def test_accounts_sessions_disable_and_password():
    _run(parity.test_accounts_sessions_disable_and_password.scenario)


def test_conversations_owner_isolation_and_messages():
    _run(parity.test_conversations_owner_isolation_and_messages.scenario)


def test_regulatory_versions_pending_and_state():
    _run(parity.test_regulatory_versions_pending_and_state.scenario)


def test_expired_session_is_rejected():
    async def scen(e):
        await e.accounts.create_company("acme", "Acme")
        u = await e.accounts.create_user("ali", "correct-horse-1", "user", None, "acme")
        tok, _ = await e.accounts.create_session(u["id"])
        assert await e.accounts.get_session_user(tok) is None
    _run(scen, ttl=-1)
