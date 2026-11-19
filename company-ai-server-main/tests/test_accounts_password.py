import asyncio

from app.database.accounts import AccountsRepository


def _go(coro):
    return asyncio.run(coro)


async def _setup():
    repo = AccountsRepository(":memory:", session_ttl_hours=1, scrypt_n=2 ** 10)
    await repo.init()
    await repo.create_company("testco", "Test Co")
    user = await repo.create_user("alice", "old-password-1", "user", None, "testco")
    return repo, user


def _error(coro_fn):
    try:
        asyncio.run(coro_fn())
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected ValueError")


def test_change_password_flow_and_session_revocation():
    async def run():
        repo, user = await _setup()
        t1, _ = await repo.create_session(user["id"])
        t2, _ = await repo.create_session(user["id"])

        assert await repo.change_password("alice", "old-password-1", "new-password-22") == user["id"]

        assert await repo.get_session_user(t1) is None and await repo.get_session_user(t2) is None
        assert await repo.authenticate("alice", "old-password-1") is None
        assert (await repo.authenticate("alice", "new-password-22"))["username"] == "alice"
    _go(run())


def test_change_password_errors():
    async def case(current, new, username="alice"):
        repo, _ = await _setup()
        await repo.change_password(username, current, new)

    assert "incorrect" in _error(lambda: case("WRONG-password", "new-password-22"))
    assert "characters" in _error(lambda: case("old-password-1", "short"))
    assert "different" in _error(lambda: case("old-password-1", "old-password-1"))
    assert "user accounts" in _error(lambda: case("x", "new-password-22", username="ghost"))   # API-key client
