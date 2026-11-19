"""In-memory token bucket (per process). Multi-worker => Redis backend chahiye."""
import time
from collections import OrderedDict


class TokenBucketLimiter:

    def __init__(
        self,
        per_minute: int,
        burst: int,
        clock=time.monotonic,
        max_keys: int = 10000,
    ):
        if per_minute <= 0 or burst <= 0:
            raise ValueError("per_minute and burst must be positive")
        self.rate = per_minute / 60.0
        self.burst = burst
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def allow(self, key: str) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)

        if tokens >= 1.0:
            self._store(key, tokens - 1.0, now)
            return True, 0.0

        self._store(key, tokens, now)
        return False, (1.0 - tokens) / self.rate

    def _store(self, key, tokens, now):
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
