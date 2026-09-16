"""弹性能力测试：令牌桶限流与 TTL+LRU 缓存。"""

from __future__ import annotations

import asyncio

import pytest

from resilience import CacheStats, TokenBucketLimiter, TTLCache

# ------------------------------------------------------------------ 限流

async def test_token_bucket_enforces_burst_then_refills():
    limiter = TokenBucketLimiter(rate_per_minute=60, burst=3)
    verdicts = [(await limiter.acquire("caller")).allowed for _ in range(4)]
    assert verdicts == [True, True, True, False]

    denied = await limiter.acquire("caller")
    assert denied.allowed is False
    assert denied.retry_after_seconds > 0


async def test_token_bucket_isolates_callers():
    limiter = TokenBucketLimiter(rate_per_minute=60, burst=1)
    assert (await limiter.acquire("a")).allowed is True
    assert (await limiter.acquire("a")).allowed is False
    assert (await limiter.acquire("b")).allowed is True


async def test_token_bucket_wait_for_succeeds_after_refill():
    limiter = TokenBucketLimiter(rate_per_minute=6000, burst=1)
    assert (await limiter.acquire("k")).allowed is True
    assert await limiter.wait_for("k", timeout=1.0) is True


async def test_token_bucket_stats_and_validation():
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate_per_minute=0)

    limiter = TokenBucketLimiter(rate_per_minute=60, burst=2)
    await limiter.acquire("x")
    await limiter.acquire("x")
    await limiter.acquire("x")
    stats = limiter.stats()["x"]
    assert stats["acquired"] == 2
    assert stats["rejected"] == 1
    assert limiter.tracked_keys == 1


# ------------------------------------------------------------------ 缓存

async def test_cache_hit_miss_and_ttl_expiry():
    cache: TTLCache[str] = TTLCache(ttl_seconds=0.05, max_entries=4)
    assert await cache.get("missing") is None

    await cache.set("k", "v")
    assert await cache.get("k") == "v"

    await asyncio.sleep(0.06)
    assert await cache.get("k") is None

    stats = await cache.stats()
    assert stats.expirations == 1
    assert stats.hit_rate == pytest.approx(1 / 3, abs=0.01)


async def test_cache_lru_eviction():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=2)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.get("a")  # 触碰 a，使 b 成为最久未使用
    await cache.set("c", 3)

    assert await cache.get("b") is None
    assert await cache.get("a") == 1
    assert (await cache.stats()).evictions == 1


async def test_cache_get_or_load_and_invalidate():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    calls = []

    def loader() -> int:
        calls.append(1)
        return 42

    assert await cache.get_or_load("k", loader) == 42
    assert await cache.get_or_load("k", loader) == 42
    assert len(calls) == 1  # 第二次命中缓存

    assert await cache.invalidate("k") is True
    assert await cache.invalidate("k") is False


async def test_cache_invalidate_prefix():
    cache: TTLCache[str] = TTLCache(ttl_seconds=60)
    await cache.set("tool:a", "1")
    await cache.set("tool:b", "2")
    await cache.set("other", "3")
    assert await cache.invalidate_prefix("tool:") == 2
    assert await cache.get("other") == "3"


def test_cache_rejects_invalid_config():
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        TTLCache(max_entries=0)


def test_cache_stats_hit_rate_defaults_to_zero():
    assert CacheStats().hit_rate == 0.0
