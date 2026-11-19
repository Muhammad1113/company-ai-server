import asyncio
import time

from app.database.accounts import AccountsRepository, hash_password, verify_password


def _new():
    return AccountsRepository(":memory:", session_ttl_hours=1, scrypt_n=2 ** 10)   # tests ke liye sasta scrypt


async def _repo():
    repo = _new()
    await repo.init()
    await repo.create_company("testco", "Test Co")      # har non-admin user kisi company ka hota hai
    return repo


def _raises(coro_fn, exc=ValueError):
    try:
        asyncio.run(coro_fn())
    except exc as e:
        return str(e)
    raise AssertionError("expected " + exc.__name__)


def test_password_hash_roundtrip_and_salting():
    a, b = hash_password("correct horse", n=2 ** 10), hash_password("correct horse", n=2 ** 10)
    assert a != b                                              # salt alag
    assert verify_password("correct horse", a) and not verify_password("wrong", a)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$2$3$4$5")


def test_create_and_authenticate():
    async def run():
        repo = await _repo()
        assert repo.has_users is False
        user = await repo.create_user("Alice", "correct-horse-1", "user", None, "testco")
        assert repo.has_users is True and user["company"] == "testco" and user["collections"] is None

        assert (await repo.authenticate("alice", "correct-horse-1"))["username"] == "Alice"   # case-insensitive
        assert await repo.authenticate("alice", "wrong-password") is None
        assert await repo.authenticate("nobody", "correct-horse-1") is None
    asyncio.run(run())


def test_validation_and_duplicates():
    repo = _new()
    asyncio.run(repo.init())
    asyncio.run(repo.create_company("testco", "Test Co"))
    mk = lambda name, pw, role="user", cols=None, co="testco": repo.create_user(name, pw, role, cols, co)  # noqa: E731
    assert "username" in _raises(lambda: mk("a b", "correct-horse-1"))
    assert "password" in _raises(lambda: mk("alice", "short"))
    assert "role" in _raises(lambda: mk("alice", "correct-horse-1", "root"))
    assert "collection" in _raises(lambda: mk("alice", "correct-horse-1", "user", ["bad name"]))   # format check
    assert "company" in _raises(lambda: mk("alice", "correct-horse-1", co=None))              # company ke baghair user nahi
    assert "company" in _raises(lambda: mk("alice", "correct-horse-1", co="nosuch"))
    asyncio.run(mk("alice", "correct-horse-1"))
    assert "exists" in _raises(lambda: mk("ALICE", "another-password"))
    # company user par haath ki collections list create ke waqt ignore hoti hai (access company se aati hai)
    assert asyncio.run(mk("bob", "correct-horse-1", "user", ["hr"]))["collections"] is None


def test_session_lifecycle():
    async def run():
        repo = await _repo()
        user = await repo.create_user("alice", "correct-horse-1", "user", None, "testco")
        token, expires = await repo.create_session(user["id"])

        assert expires > time.time() and len(token) >= 40
        assert (await repo.get_session_user(token))["username"] == "alice"
        assert await repo.get_session_user("not-a-token") is None
        assert await repo.get_session_user("") is None

        await repo.delete_session(token)
        assert await repo.get_session_user(token) is None
    asyncio.run(run())


def test_token_is_not_stored_in_plaintext():
    async def run():
        repo = await _repo()
        user = await repo.create_user("alice", "correct-horse-1", "user", None, "testco")
        token, _ = await repo.create_session(user["id"])
        rows = repo._conn.execute("SELECT token_hash FROM sessions").fetchall()
        assert rows and all(token not in r[0] for r in rows)
    asyncio.run(run())


def test_expired_session_rejected():
    async def run():
        repo = await _repo()
        user = await repo.create_user("alice", "correct-horse-1", "user", None, "testco")
        token, _ = await repo.create_session(user["id"])
        repo._conn.execute("UPDATE sessions SET expires_at = ?", (time.time() - 5,))
        assert await repo.get_session_user(token) is None
    asyncio.run(run())


def test_disable_and_password_change_kill_sessions():
    async def run():
        repo = await _repo()
        user = await repo.create_user("alice", "correct-horse-1", "user", None, "testco")
        t1, _ = await repo.create_session(user["id"])

        assert await repo.update_user("alice", password="brand-new-password")
        assert await repo.get_session_user(t1) is None                       # purana session band
        assert await repo.authenticate("alice", "correct-horse-1") is None
        assert await repo.authenticate("alice", "brand-new-password")

        t2, _ = await repo.create_session(user["id"])
        assert await repo.update_user("alice", disabled=True)
        assert await repo.get_session_user(t2) is None
        assert await repo.authenticate("alice", "brand-new-password") is None   # disabled login nahi kar sakta

        assert await repo.update_user("alice", disabled=False, role="company_admin")
        users = await repo.list_users()
        assert users[0]["role"] == "company_admin" and users[0]["collections"] is None
        try:
            await repo.update_user("alice", collections=["hr"])                                  # company user: access company se
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "collections" in str(exc)
        assert await repo.update_user("alice", collections=None) and (await repo.list_users())[0]["collections"] is None
        assert await repo.update_user("ghost", role="admin") is False
    asyncio.run(run())


def test_uninitialized_repo_is_safe():
    async def run():
        repo = AccountsRepository(":memory:", scrypt_n=2 ** 10)
        assert await repo.authenticate("a", "b") is None
        assert await repo.get_session_user("token") is None
        assert repo.has_users is False
    asyncio.run(run())
