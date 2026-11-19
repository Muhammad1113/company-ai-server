import asyncio
from collections import deque
from contextlib import asynccontextmanager
from time import perf_counter

from app.core.config import settings
from app.core.metrics import (
    ACTIVE_INFERENCES,
    INFERENCE_DURATION,
    INFERENCE_TOTAL,
    QUEUE_WAIT_DURATION,
    QUEUED_REQUESTS,
)


class InferenceQueueFull(Exception):
    pass


class InferenceQueueTimeout(Exception):
    pass


class InferenceController:
    def __init__(self):
        self.max_concurrent = settings.INFERENCE_MAX_CONCURRENT
        self.max_queue_size = settings.INFERENCE_MAX_QUEUE_SIZE
        self.active = 0
        self.waiters = deque()

    async def acquire(self):
        started = perf_counter()

        # Immediate admission only if nobody is already waiting.
        if (
            self.active < self.max_concurrent
            and not self.waiters
        ):
            self.active += 1
            ACTIVE_INFERENCES.inc()
            QUEUE_WAIT_DURATION.observe(0)
            return

        if len(self.waiters) >= self.max_queue_size:
            raise InferenceQueueFull()

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.waiters.append(future)
        QUEUED_REQUESTS.inc()

        try:
            await asyncio.wait_for(
                asyncio.shield(future), timeout=settings.INFERENCE_QUEUE_TIMEOUT
            )
        except TimeoutError:
            if future.done():
                # Slot was granted at the timeout boundary.
                QUEUE_WAIT_DURATION.observe(
                    perf_counter() - started
                )
                return
            self._remove_waiter(future)
            raise InferenceQueueTimeout()
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # A slot was granted just before cancellation.
                self.release()
            else:
                self._remove_waiter(future)
            raise
        
        # Duration metric for successfully acquired slot after waiting
        QUEUE_WAIT_DURATION.observe(perf_counter() - started)

    def _remove_waiter(self, future):
        try:
            self.waiters.remove(future)
            QUEUED_REQUESTS.dec()
        except ValueError:
            # Already handed a slot to this request.
            pass

    def release(self):
        while self.waiters:
            next_waiter = self.waiters.popleft()
            if next_waiter.cancelled() or next_waiter.done():
                continue
            QUEUED_REQUESTS.dec()
            next_waiter.set_result(True)
            # Active count remains unchanged:
            # the next waiting request takes this slot.
            return

        self.active -= 1
        ACTIVE_INFERENCES.dec()

    @asynccontextmanager
    async def slot(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()

    async def execute(self, operation):
        async with self.slot():
            try:
                with INFERENCE_DURATION.time():
                    result = await operation()
                INFERENCE_TOTAL.labels(status="success").inc()
                return result
            except Exception:
                INFERENCE_TOTAL.labels(status="error").inc()
                raise


inference_controller = InferenceController()
