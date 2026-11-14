"""
Per-client concurrency cap: ek user poori GPU queue na bhar sake.
Sync + single event loop => race nahi. max<=0 => band.
"""


class ClientBusy(Exception):
    pass


class ClientConcurrencyLimiter:

    def __init__(self, max_per_client: int):
        self.max = max_per_client
        self._active: dict[str, int] = {}

    def acquire(self, key: str) -> None:
        if self.max <= 0:
            return
        count = self._active.get(key, 0)
        if count >= self.max:
            raise ClientBusy()
        self._active[key] = count + 1

    def release(self, key: str) -> None:
        if self.max <= 0:
            return
        count = self._active.get(key, 0) - 1
        if count <= 0:
            self._active.pop(key, None)
        else:
            self._active[key] = count
