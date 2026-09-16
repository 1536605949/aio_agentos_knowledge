"""资源锁管理器。

解决多 Agent 抢占同一资源（同一服务、同一设备、同一工单）的冲突：

- **互斥**：同一 ``resource`` 同一时刻只允许一个持有者。
- **公平排队**：等待者按 FIFO 顺序获得锁，避免饥饿。
- **超时**：等待超时抛 :class:`ResourceLockTimeout`，调用方可据此降级。
- **可观测**：暴露等待队列深度与持有统计，供 ``/metrics`` 使用。
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


class ResourceLockError(RuntimeError):
    """资源锁相关错误基类。"""


class ResourceLockTimeout(ResourceLockError):
    """在超时时间内未能获得资源锁。"""


@dataclass
class LockStats:
    acquisitions: int = 0
    timeouts: int = 0
    current_holders: int = 0
    queued: int = 0


@dataclass
class _ResourceState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: deque[str] = field(default_factory=deque)
    acquisitions: int = 0
    timeouts: int = 0


class ResourceLockManager:
    """按资源名隔离的异步互斥锁，带 FIFO 排队与超时。"""

    def __init__(self, default_timeout_seconds: float = 10.0) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self._resources: dict[str, _ResourceState] = {}
        self._guard = asyncio.Lock()

    async def _state(self, resource: str) -> _ResourceState:
        async with self._guard:
            state = self._resources.get(resource)
            if state is None:
                state = _ResourceState()
                self._resources[resource] = state
            return state

    @asynccontextmanager
    async def acquire(
        self,
        resource: str,
        holder: str = "anonymous",
        timeout: float | None = None,
    ) -> AsyncIterator[None]:
        """获取资源锁。用法::

            async with locks.acquire("service:checkout", holder="remediation"):
                await do_something()
        """
        effective_timeout = self.default_timeout_seconds if timeout is None else timeout
        state = await self._state(resource)
        state.waiters.append(holder)
        try:
            await asyncio.wait_for(state.lock.acquire(), timeout=effective_timeout)
        except TimeoutError as exc:
            state.timeouts += 1
            raise ResourceLockTimeout(
                f"timed out after {effective_timeout}s waiting for resource '{resource}' "
                f"(holder={holder}, queued={len(state.waiters)})"
            ) from exc
        finally:
            try:
                state.waiters.remove(holder)
            except ValueError:
                pass

        state.acquisitions += 1
        try:
            yield
        finally:
            state.lock.release()

    def is_locked(self, resource: str) -> bool:
        state = self._resources.get(resource)
        return bool(state and state.lock.locked())

    def stats(self) -> dict[str, LockStats]:
        return {
            resource: LockStats(
                acquisitions=state.acquisitions,
                timeouts=state.timeouts,
                current_holders=1 if state.lock.locked() else 0,
                queued=len(state.waiters),
            )
            for resource, state in sorted(self._resources.items())
        }

    @property
    def contended_resources(self) -> list[str]:
        return [resource for resource, state in self._resources.items() if len(state.waiters) > 0]
