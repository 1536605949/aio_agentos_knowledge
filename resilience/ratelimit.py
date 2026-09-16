"""令牌桶限流器。

用于保护两类资源：
- **入站**：API 端点按调用方限流（``rate_limit`` 依赖）。
- **出站**：LLM 与外部 Tool 调用按上游配额限流。

采用惰性补充（lazy refill）实现，无需后台任务，适合无状态多副本部署
（状态在进程内时每副本独立配额，生产可替换为 Redis 实现同一接口）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: float
    retry_after_seconds: float = 0.0
    reason: str = ""


@dataclass
class _Bucket:
    tokens: float
    updated_at: float
    acquired: int = 0
    rejected: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TokenBucketLimiter:
    """按 key（调用方 / 工具名）隔离的令牌桶。"""

    def __init__(self, rate_per_minute: int = 120, burst: int = 30) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate_per_second = rate_per_minute / 60.0
        self.capacity = max(1, burst)
        self._buckets: dict[str, _Bucket] = {}
        self._guard = asyncio.Lock()

    async def _bucket(self, key: str) -> _Bucket:
        async with self._guard:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), updated_at=monotonic())
                self._buckets[key] = bucket
            return bucket

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(self.capacity), bucket.tokens + elapsed * self.rate_per_second)
        bucket.updated_at = now

    async def acquire(self, key: str, tokens: float = 1.0) -> RateLimitDecision:
        """尝试取走 ``tokens`` 个令牌。非阻塞，立即返回判定结果。"""
        bucket = await self._bucket(key)
        async with bucket._lock:
            now = monotonic()
            self._refill(bucket, now)
            if bucket.tokens >= tokens:
                bucket.tokens -= tokens
                bucket.acquired += 1
                return RateLimitDecision(allowed=True, remaining=round(bucket.tokens, 3))
            deficit = tokens - bucket.tokens
            retry_after = deficit / self.rate_per_second if self.rate_per_second > 0 else 60.0
            bucket.rejected += 1
            return RateLimitDecision(
                allowed=False,
                remaining=round(bucket.tokens, 3),
                retry_after_seconds=round(retry_after, 3),
                reason=f"rate limit exceeded for {key}",
            )

    async def wait_for(self, key: str, tokens: float = 1.0, timeout: float | None = None) -> bool:
        """阻塞直到取得令牌，或超时返回 False。"""
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            decision = await self.acquire(key, tokens)
            if decision.allowed:
                return True
            if deadline is not None and monotonic() >= deadline:
                return False
            await asyncio.sleep(min(max(decision.retry_after_seconds, 0.01), 1.0))

    def stats(self) -> dict[str, dict[str, float | int]]:
        return {
            key: {
                "tokens": round(bucket.tokens, 3),
                "capacity": self.capacity,
                "acquired": bucket.acquired,
                "rejected": bucket.rejected,
            }
            for key, bucket in sorted(self._buckets.items())
        }

    @property
    def tracked_keys(self) -> int:
        return len(self._buckets)
