import asyncio

from app.database.usage import UsageRepository


def test_usage_record_and_summary():
    async def run():
        repo = UsageRepository(":memory:")
        await repo.init()

        await repo.record(client="alice", endpoint="completions", status="success",
                          prompt_tokens=10, completion_tokens=20, latency_ms=100)
        await repo.record(client="alice", endpoint="completions", status="success",
                          prompt_tokens=5, completion_tokens=5, latency_ms=300,
                          cache="hit")
        await repo.record(client="alice", endpoint="stream", status="cancelled")
        await repo.record(client="bob", endpoint="completions", status="success")

        s = await repo.summary("alice")
        assert s["requests"] == 3
        assert s["prompt_tokens"] == 15 and s["completion_tokens"] == 25
        assert s["avg_latency_ms"] == 200.0
        assert s["by_status"] == {"success": 2, "cancelled": 1}
        assert (await repo.summary("bob"))["requests"] == 1
        assert (await repo.summary("nobody"))["requests"] == 0
        await repo.close()
    asyncio.run(run())


def test_uninitialized_repo_is_safe():
    async def run():
        repo = UsageRepository(":memory:")
        await repo.record(client="x", endpoint="e", status="s")   # no-op
        assert (await repo.summary("x"))["requests"] == 0
    asyncio.run(run())
