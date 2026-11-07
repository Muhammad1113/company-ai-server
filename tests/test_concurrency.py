import asyncio

import pytest

from app.core.config import settings
from app.core.concurrency import (
    InferenceController,
    InferenceQueueFull,
    InferenceQueueTimeout
)


def make_controller(monkeypatch, queue_size=2, timeout=2):

    monkeypatch.setattr(
        settings, "INFERENCE_MAX_CONCURRENT", 1
    )

    monkeypatch.setattr(
        settings, "INFERENCE_MAX_QUEUE_SIZE", queue_size
    )

    monkeypatch.setattr(
        settings, "INFERENCE_QUEUE_TIMEOUT", timeout
    )

    return InferenceController()


def test_fifo_order(monkeypatch):

    async def run():

        controller = make_controller(monkeypatch)
        order = []

        async def worker(name, delay=0):

            async with controller.slot():
                order.append(name)
                await asyncio.sleep(delay)

        first = asyncio.create_task(worker("first", 0.05))

        await asyncio.sleep(0.01)

        second = asyncio.create_task(worker("second"))
        await asyncio.sleep(0.01)

        third = asyncio.create_task(worker("third"))

        await asyncio.gather(first, second, third)

        assert order == ["first", "second", "third"]
        assert controller.active == 0
        assert len(controller.waiters) == 0

    asyncio.run(run())


def test_queue_full(monkeypatch):

    async def run():

        controller = make_controller(
            monkeypatch,
            queue_size=1
        )

        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():

            async with controller.slot():
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())

        await entered.wait()

        waiting = asyncio.create_task(
            controller.acquire()
        )

        await asyncio.sleep(0.01)

        with pytest.raises(InferenceQueueFull):
            await controller.acquire()

        release.set()

        await task
        await waiting

        controller.release()

        assert controller.active == 0

    asyncio.run(run())


def test_queue_timeout(monkeypatch):

    async def run():

        controller = make_controller(
            monkeypatch,
            queue_size=2,
            timeout=0.05
        )

        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():

            async with controller.slot():
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())

        await entered.wait()

        with pytest.raises(InferenceQueueTimeout):
            await controller.acquire()

        release.set()
        await task

        assert controller.active == 0
        assert len(controller.waiters) == 0

    asyncio.run(run())


def test_cancelled_waiter(monkeypatch):

    async def run():

        controller = make_controller(monkeypatch)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():

            async with controller.slot():
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())

        await entered.wait()

        waiting = asyncio.create_task(
            controller.acquire()
        )

        await asyncio.sleep(0.01)

        waiting.cancel()

        with pytest.raises(asyncio.CancelledError):
            await waiting

        release.set()
        await task

        assert controller.active == 0
        assert len(controller.waiters) == 0

    asyncio.run(run())