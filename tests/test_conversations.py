import asyncio

from app.database.conversations import ConversationRepository, DEFAULT_TITLE, title_from


async def _repo():
    repo = ConversationRepository(":memory:")
    await repo.init()
    return repo


def test_owner_isolation_and_crud():
    async def run():
        repo = await _repo()
        a = await repo.create("alice")
        await repo.create("bob")

        assert (await repo.get(a["id"], "alice"))["title"] == DEFAULT_TITLE
        assert await repo.get(a["id"], "bob") is None                      # doosre ka nahi dikhta
        assert await repo.rename(a["id"], "bob", "hacked") is False
        assert await repo.delete(a["id"], "bob") is False

        assert await repo.rename(a["id"], "alice", "Leave questions")
        assert [c["title"] for c in await repo.list_conversations("alice")] == ["Leave questions"]
        assert len(await repo.list_conversations("bob")) == 1

        assert await repo.delete(a["id"], "alice")
        assert await repo.get(a["id"], "alice") is None and await repo.list_conversations("alice") == []
    asyncio.run(run())


def test_messages_order_limit_and_cascade():
    async def run():
        repo = await _repo()
        c = await repo.create("alice")
        for i in range(6):
            await repo.add_message(c["id"], "user" if i % 2 == 0 else "assistant", f"m{i}")

        assert [m["content"] for m in await repo.list_messages(c["id"])] == [f"m{i}" for i in range(6)]
        assert [m["content"] for m in await repo.list_messages(c["id"], limit=3)] == ["m3", "m4", "m5"]

        await repo.delete(c["id"], "alice")
        assert await repo.list_messages(c["id"]) == []                      # messages bhi gaye
    asyncio.run(run())


def test_delete_last_assistant_only():
    async def run():
        repo = await _repo()
        c = await repo.create("alice")
        await repo.add_message(c["id"], "user", "q")
        assert await repo.delete_last_assistant(c["id"]) is False           # last user hai
        await repo.add_message(c["id"], "assistant", "a", status="stopped")
        assert await repo.delete_last_assistant(c["id"]) is True
        assert [m["role"] for m in await repo.list_messages(c["id"])] == ["user"]
    asyncio.run(run())


def test_title_helpers_and_recency_order():
    async def run():
        repo = await _repo()
        first = await repo.create("alice")
        second = await repo.create("alice")
        await repo.set_title_if_default(first["id"], title_from("What is the refund policy?"))
        await repo.set_title_if_default(first["id"], "should not overwrite")
        await repo.add_message(first["id"], "user", "x")                    # first ab sab se naya

        listed = await repo.list_conversations("alice")
        assert listed[0]["id"] == first["id"] and listed[0]["title"] == "What is the refund policy?"
        assert listed[1]["id"] == second["id"]
        assert len(await repo.list_conversations("alice", limit=1)) == 1
        assert len(await repo.list_conversations("alice", limit=5, offset=1)) == 1

        assert title_from("x" * 100).endswith("…") and len(title_from("x" * 100)) == 61
        assert title_from("   \n  ") == DEFAULT_TITLE and title_from("line1\nline2") == "line1"
    asyncio.run(run())
