"""带 TTL 与容量上限的内存缓存。

分层策略（与 ``docs/architecture.md`` 的"多级缓存"对应）：

- **L1 工具结果缓存**：只读类 Tool 的输出，TTL 短（默认 30s），命中率高。
- **L2 本体/技能注册表**：进程内常驻，无 TTL，随启动加载一次。
- **L3 外部缓存**：生产环境替换为 Redis（实现同一接口）。

本模块提供 L1/L2 所需的实现，并把命中率统计暴露给 ``/metrics``。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    size: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0


class TTLCache(Generic[T]):
    """LRU + TTL 缓存，异步安全。"""

    def __init__(self, ttl_seconds: float = 30.0, max_entries: int = 512) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = CacheStats()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._stats.misses += 1
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                del self._entries[key]
                self._stats.expirations += 1
                self._stats.misses += 1
                return None
            self._entries.move_to_end(key)
            self._stats.hits += 1
            return value

    async def set(self, key: str, value: T, ttl_seconds: float | None = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        async with self._lock:
            if key in self._entries:
                del self._entries[key]
            self._entries[key] = (time.monotonic() + ttl, value)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._stats.evictions += 1

    async def get_or_load(self, key: str, loader: Any, ttl_seconds: float | None = None) -> T:
        """缓存未命中时调用 ``loader()``（支持协程）并回填。"""
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = loader()
        if asyncio.iscoroutine(value):
            value = await value
        await self.set(key, value, ttl_seconds)
        return value

    async def invalidate(self, key: str) -> bool:
        async with self._lock:
            return self._entries.pop(key, None) is not None

    async def invalidate_prefix(self, prefix: str) -> int:
        async with self._lock:
            victims = [key for key in self._entries if key.startswith(prefix)]
            for key in victims:
                del self._entries[key]
            return len(victims)

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()

    async def stats(self) -> CacheStats:
        async with self._lock:
            self._stats.size = len(self._entries)
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                expirations=self._stats.expirations,
                size=self._stats.size,
            )
