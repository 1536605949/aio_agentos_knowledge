"""并发控制测试：资源锁的互斥、FIFO 公平性与超时。"""

from __future__ import annotations

import asyncio

import pytest

from concurrency import ResourceLockManager, ResourceLockTimeout


async def test_resource_lock_is_mutually_exclusive():
    locks = ResourceLockManager()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.acquire("service:checkout", holder=name):
            order.append(f"{name}-in")
            await asyncio.sleep(0.01)
            order.append(f"{name}-out")

    await asyncio.gather(worker("A"), worker("B"))
    # 不会出现交错的 in/in/out/out
    assert order in (
        ["A-in", "A-out", "B-in", "B-out"],
        ["B-in", "B-out", "A-in", "A-out"],
    )


async def test_resource_lock_is_fifo_fair():
    locks = ResourceLockManager()
    order: list[str] = []

    async def holder() -> None:
        async with locks.acquire("r", holder="holder"):
            order.append("holder-in")
            await asyncio.sleep(0.05)
            order.append("holder-out")

    async def waiter(name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        async with locks.acquire("r", holder=name):
            order.append(name)

    # holder 先持锁；A 先排队，B 后排队
    await asyncio.gather(holder(), waiter("A", 0.01), waiter("B", 0.02))
    assert order == ["holder-in", "holder-out", "A", "B"]


async def test_resource_lock_times_out():
    locks = ResourceLockManager(default_timeout_seconds=5.0)

    async def holder() -> None:
        async with locks.acquire("r", holder="holder"):
            await asyncio.sleep(0.3)

    async def waiter() -> None:
        await asyncio.sleep(0.01)
        with pytest.raises(ResourceLockTimeout) as excinfo:
            async with locks.acquire("r", holder="waiter", timeout=0.05):
                pass
        assert "queued=" in str(excinfo.value)

    await asyncio.gather(holder(), waiter())


async def test_resource_lock_stats_and_contention():
    locks = ResourceLockManager()
    async with locks.acquire("r", holder="a"):
        assert locks.is_locked("r") is True
    assert locks.is_locked("r") is False

    stats = locks.stats()["r"]
    assert stats.acquisitions == 1
    assert stats.timeouts == 0
    assert stats.current_holders == 0
    assert locks.contended_resources == []


async def test_different_resources_do_not_block_each_other():
    locks = ResourceLockManager()
    entered: list[str] = []

    async def worker(name: str) -> None:
        async with locks.acquire(f"service:{name}", holder=name):
            entered.append(name)
            await asyncio.sleep(0.05)

    await asyncio.wait_for(asyncio.gather(worker("a"), worker("b"), worker("c")), timeout=0.3)
    assert sorted(entered) == ["a", "b", "c"]
