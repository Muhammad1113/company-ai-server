import asyncio

from app.core.cache import MemoryTTLCache, ResponseCache


def test_memory_cache_ttl_and_lru():
    async def run():
        now = [0.0]
        cache = MemoryTTLCache(max_entries=2, clock=lambda: now[0])

        await cache.set("a", "1", ttl=10)
        await cache.set("b", "2", ttl=10)
        assert await cache.get("a") == "1"      # a recent ho gaya
        await cache.set("c", "3", ttl=10)       # b evict
        assert await cache.get("b") is None
        assert await cache.get("a") == "1"

        now[0] = 11.0                           # TTL expire
        assert await cache.get("a") is None

    asyncio.run(run())


def test_hit_after_miss():
    async def run():
        cache = ResponseCache(MemoryTTLCache(), ttl=60)
        calls = 0

        async def compute():
            nonlocal calls
            calls += 1
            return {"response": "hi"}

        v1, s1 = await cache.get_or_compute("k", compute)
        v2, s2 = await cache.get_or_compute("k", compute)
        assert (s1, s2) == ("miss", "hit")
        assert v1 == v2 == {"response": "hi"}
        assert calls == 1

    asyncio.run(run())


def test_single_flight_dedupes_concurrent_requests():
    async def run():
        cache = ResponseCache(MemoryTTLCache(), ttl=60)
        calls = 0

        async def compute():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"n": calls}

        results = await asyncio.gather(
            *[cache.get_or_compute("k", compute) for _ in range(5)]
        )
        assert calls == 1
        assert sorted(s for _, s in results) == ["miss"] + ["shared"] * 4
        assert all(v == {"n": 1} for v, _ in results)
        assert cache._inflight == {}

    asyncio.run(run())


def test_leader_cancel_does_not_break_followers():
    async def run():
        cache = ResponseCache(MemoryTTLCache(), ttl=60)

        async def compute():
            await asyncio.sleep(0.1)
            return {"ok": True}

        leader = asyncio.create_task(cache.get_or_compute("k", compute))
        await asyncio.sleep(0.01)
        follower = asyncio.create_task(cache.get_or_compute("k", compute))
        await asyncio.sleep(0.01)

        leader.cancel()
        value, source = await follower
        assert value == {"ok": True} and source == "shared"

    asyncio.run(run())


def test_all_waiters_cancelled_cancels_compute():
    async def run():
        cache = ResponseCache(MemoryTTLCache(), ttl=60)
        state = {"cancelled": False}

        async def compute():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        t = asyncio.create_task(cache.get_or_compute("k", compute))
        await asyncio.sleep(0.02)
        t.cancel()
        await asyncio.sleep(0.05)

        assert state["cancelled"] is True
        assert cache._inflight == {}

    asyncio.run(run())


def test_compute_error_not_cached_and_propagates():
    async def run():
        cache = ResponseCache(MemoryTTLCache(), ttl=60)

        async def bad():
            raise RuntimeError("boom")

        for _ in range(2):
            try:
                await cache.get_or_compute("k", bad)
                assert False, "should raise"
            except RuntimeError:
                pass

        async def good():
            return {"ok": 1}

        assert (await cache.get_or_compute("k", good))[1] == "miss"

    asyncio.run(run())


def test_backend_failure_degrades_to_miss():
    class Broken:
        async def get(self, key): raise ConnectionError("redis down")
        async def set(self, key, value, ttl): raise ConnectionError("redis down")
        async def close(self): pass

    async def run():
        cache = ResponseCache(Broken(), ttl=60)

        async def compute():
            return {"ok": 1}

        value, source = await cache.get_or_compute("k", compute)
        assert value == {"ok": 1} and source == "miss"

    asyncio.run(run())
