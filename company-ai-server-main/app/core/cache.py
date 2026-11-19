"""
Response cache + single-flight.

- MemoryTTLCache : LRU + TTL (per process)
- RedisCache     : shared cache (redis package chahiye; lazy import)
- ResponseCache  : hit/miss + identical concurrent requests ek hi inference
                   share karti hain (GPU bachta hai).
"""
import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

logger = logging.getLogger("company_ai.cache")


class MemoryTTLCache:

    def __init__(self, max_entries: int = 1000, clock=time.monotonic):
        self.max_entries = max_entries
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()

    async def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= self.clock():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl: float) -> None:
        self._data[key] = (self.clock() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    async def close(self) -> None:
        self._data.clear()


class RedisCache:

    def __init__(self, url: str, prefix: str = "company_ai:"):
        try:
            import redis.asyncio as redis
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "CACHE_BACKEND=redis needs: pip install redis"
            ) from exc
        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    async def get(self, key: str) -> str | None:
        return await self._redis.get(self._prefix + key)

    async def set(self, key: str, value: str, ttl: float) -> None:
        await self._redis.set(self._prefix + key, value, ex=max(1, int(ttl)))

    async def close(self) -> None:
        closer = getattr(self._redis, "aclose", None) or self._redis.close
        await closer()


class _Flight:
    def __init__(self, task: asyncio.Task):
        self.task = task
        self.waiters = 0


class ResponseCache:

    def __init__(self, backend, ttl: float):
        self.backend = backend
        self.ttl = ttl
        self._inflight: dict[str, _Flight] = {}

    async def get_or_compute(
        self,
        key: str,
        compute: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, str]:
        """Returns (value, source) ; source = hit | miss | shared."""

        try:
            cached = await self.backend.get(key)
        except Exception:
            logger.exception("cache get failed; treating as miss")
            cached = None

        if cached is not None:
            return json.loads(cached), "hit"

        flight = self._inflight.get(key)
        source = "shared"

        if flight is None:
            task = asyncio.create_task(self._run(key, compute))
            # exception "never retrieved" warning se bachne ke liye
            task.add_done_callback(
                lambda t: t.cancelled() or t.exception()
            )
            flight = _Flight(task)
            self._inflight[key] = flight
            source = "miss"

        flight.waiters += 1
        try:
            # shield: ek waiter cancel ho to baaki waiters ka kaam na ruke
            return await asyncio.shield(flight.task), source

        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                # Koi intezar nahi kar raha: inference cancel (GPU slot free)
                if self._inflight.get(key) is flight:
                    del self._inflight[key]
                flight.task.cancel()

    async def _run(self, key: str, compute):
        try:
            value = await compute()
            try:
                await self.backend.set(key, json.dumps(value), self.ttl)
            except Exception:
                logger.exception("cache set failed")
            return value
        finally:
            self._inflight.pop(key, None)

    async def close(self) -> None:
        await self.backend.close()
