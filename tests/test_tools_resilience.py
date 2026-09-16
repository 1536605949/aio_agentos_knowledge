"""工具层测试：治理拦截位置、重试分级、幂等、缓存、限流、熔断、资源锁。

这些用例覆盖的都是**原实现存在缺陷或完全缺失**的行为，因此它们是回归保护的重点。
"""

from __future__ import annotations

import asyncio

import pytest

from concurrency import ResourceLockManager
from governance.models import Principal, RiskLevel
from observability import AgentTrace, BadCaseCategory, BadCaseCollector
from persistence import InMemoryDocumentStore
from resilience import TokenBucketLimiter, TTLCache
from tools import (
    CircuitOpen,
    PermanentToolError,
    ToolError,
    ToolNotFound,
    ToolPolicyDenied,
    ToolRateLimited,
    ToolRegistry,
    ToolSpec,
    TransientToolError,
)

VIEWER = Principal(subject="viewer", roles={"viewer"})
ADMIN = Principal(subject="admin", roles={"admin"})
APPROVER = Principal(subject="approver", roles={"approver"})


def _spec(name: str = "svc.do", **overrides) -> ToolSpec:
    defaults = {
        "name": name,
        "risk_level": RiskLevel.LOW,
        "required_permission": "tool:read",
        "timeout_seconds": 1.0,
        "max_retries": 0,
    }
    defaults.update(overrides)
    return ToolSpec(**defaults)


# ---------------------------------------------------------------- 治理

async def test_unknown_tool_is_rejected():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFound):
        await registry.invoke("nope", {}, VIEWER, AgentTrace())


async def test_policy_denial_is_not_masked_by_retries():
    """策略拒绝必须发生在重试循环之外——否则会被当成瞬时故障反复重试。"""
    calls = []

    async def handler(payload):
        calls.append(payload)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(_spec("service.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high"), handler)

    with pytest.raises(ToolPolicyDenied) as excinfo:
        await registry.invoke("service.restart", {}, ADMIN, AgentTrace(), approved=False)
    assert "approval" in str(excinfo.value)
    assert calls == []
    assert registry.stats()["service.restart"]["policy_denials"] == 1


async def test_approver_role_cannot_execute_high_risk_tool():
    """职责分离：审批者只有 approval:decide，没有 tool:high。"""
    registry = ToolRegistry()
    registry.register(_spec("service.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high"), _echo)

    with pytest.raises(ToolPolicyDenied) as excinfo:
        await registry.invoke("service.restart", {}, APPROVER, AgentTrace(), approved=True)
    assert "tool:high" in str(excinfo.value)


async def test_missing_permission_is_denied():
    registry = ToolRegistry()
    registry.register(_spec("svc.medium", risk_level=RiskLevel.MEDIUM, required_permission="tool:medium"), _echo)
    with pytest.raises(ToolPolicyDenied):
        await registry.invoke("svc.medium", {}, VIEWER, AgentTrace())


# ------------------------------------------------------------ 重试分级

async def _echo(payload):
    return {"echo": payload}


async def test_transient_error_is_retried_then_succeeds():
    attempts = {"n": 0}

    async def flaky(payload):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientToolError(f"transient #{attempts['n']}")
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(_spec("flaky", max_retries=3), flaky)

    result = await registry.invoke("flaky", {}, VIEWER, AgentTrace())
    assert result == {"ok": True}
    assert attempts["n"] == 3
    assert registry.stats()["flaky"]["retries"] == 2


async def test_permanent_error_is_not_retried():
    attempts = {"n": 0}

    async def broken(payload):
        attempts["n"] += 1
        raise PermanentToolError("bad parameter")

    registry = ToolRegistry()
    registry.register(_spec("broken", max_retries=5), broken)

    with pytest.raises(ToolError):
        await registry.invoke("broken", {}, VIEWER, AgentTrace())
    assert attempts["n"] == 1  # 只尝试一次


async def test_circuit_opens_after_threshold():
    async def always_fails(payload):
        raise TransientToolError("down")

    registry = ToolRegistry(circuit_threshold=2, cooldown_seconds=30.0)
    registry.register(_spec("dead", max_retries=0), always_fails)

    for _ in range(2):
        with pytest.raises(ToolError):
            await registry.invoke("dead", {}, VIEWER, AgentTrace())

    with pytest.raises(CircuitOpen):
        await registry.invoke("dead", {}, VIEWER, AgentTrace())

    assert registry.stats()["dead"]["circuit_open"] is True
    assert registry.reset_circuit("dead") is True
    with pytest.raises(ToolError):  # 熔断已复位，重新尝试真实调用
        await registry.invoke("dead", {}, VIEWER, AgentTrace())


# -------------------------------------------------------- 幂等与缓存

async def test_idempotent_replay_does_not_re_execute():
    calls = {"n": 0}

    async def side_effect(payload):
        calls["n"] += 1
        return {"executed": calls["n"]}

    registry = ToolRegistry()
    registry.register(_spec("svc.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high", idempotent=True), side_effect)

    first = await registry.invoke("svc.restart", {}, ADMIN, AgentTrace(), approved=True, idempotency_key="k1")
    second = await registry.invoke("svc.restart", {}, ADMIN, AgentTrace(), approved=True, idempotency_key="k1")
    third = await registry.invoke("svc.restart", {}, ADMIN, AgentTrace(), approved=True, idempotency_key="k2")

    assert first == second
    assert calls["n"] == 2  # k1 只执行一次，k2 是新调用
    assert third == {"executed": 2}


async def test_idempotency_persists_through_store():
    calls = {"n": 0}

    async def side_effect(payload):
        calls["n"] += 1
        return {"executed": calls["n"]}

    store = InMemoryDocumentStore()
    registry = ToolRegistry(store=store)
    registry.register(_spec("svc.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high", idempotent=True), side_effect)

    await registry.invoke("svc.restart", {}, ADMIN, AgentTrace(), approved=True, idempotency_key="k1")

    # 新的注册表实例（模拟进程重启后重新装配）仍能命中同一幂等记录
    restarted = ToolRegistry(store=store)
    restarted.register(_spec("svc.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high", idempotent=True), side_effect)
    replay = await restarted.invoke("svc.restart", {}, ADMIN, AgentTrace(), approved=True, idempotency_key="k1")

    assert replay == {"executed": 1}
    assert calls["n"] == 1


async def test_cacheable_tool_uses_result_cache():
    calls = {"n": 0}

    async def reader(payload):
        calls["n"] += 1
        return {"value": calls["n"], "service": payload["service"]}

    cache: TTLCache = TTLCache(ttl_seconds=60)
    registry = ToolRegistry(cache=cache)
    registry.register(_spec("logs.search", cacheable=True), reader)

    first = await registry.invoke("logs.search", {"service": "a"}, VIEWER, AgentTrace())
    second = await registry.invoke("logs.search", {"service": "a"}, VIEWER, AgentTrace())
    third = await registry.invoke("logs.search", {"service": "b"}, VIEWER, AgentTrace())

    assert first == second
    assert calls["n"] == 2  # 相同 payload 命中缓存
    assert third["value"] == 2
    assert registry.stats()["logs.search"]["cache_hits"] == 1


# ------------------------------------------------------------ 限流与锁

async def test_outbound_rate_limit_blocks_excess_calls():
    registry = ToolRegistry(limiter=TokenBucketLimiter(rate_per_minute=60, burst=1))
    registry.register(_spec("svc.read"), _echo)

    await registry.invoke("svc.read", {}, VIEWER, AgentTrace())
    with pytest.raises(ToolRateLimited):
        await registry.invoke("svc.read", {}, VIEWER, AgentTrace())


async def test_resource_lock_contention_fails_fast():
    locks = ResourceLockManager(default_timeout_seconds=0.05)
    registry = ToolRegistry(locks=locks)
    registry.register(_spec("service.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high", resource_field="service"), _echo)

    async with locks.acquire("service.restart:checkout", holder="other"):
        with pytest.raises(ToolError) as excinfo:
            await registry.invoke(
                "service.restart", {"service": "checkout"}, ADMIN, AgentTrace(), approved=True
            )
        assert "busy" in str(excinfo.value)


async def test_resource_lock_released_after_call():
    locks = ResourceLockManager()
    registry = ToolRegistry(locks=locks)
    registry.register(_spec("svc.do", resource_field="service"), _echo)

    await registry.invoke("svc.do", {"service": "a"}, VIEWER, AgentTrace())
    assert locks.is_locked("svc.do:a") is False


# ------------------------------------------------------------- BadCase

async def test_failure_is_archived_as_badcase():
    badcases = BadCaseCollector()

    async def boom(payload):
        raise PermanentToolError("cannot proceed")

    registry = ToolRegistry(badcases=badcases)
    registry.register(_spec("boom"), boom)

    with pytest.raises(ToolError):
        await registry.invoke("boom", {}, VIEWER, AgentTrace())

    cases = badcases.list()
    assert len(cases) == 1
    assert cases[0].category is BadCaseCategory.TOOL_ERROR
    assert cases[0].source == "boom"


async def test_policy_denial_is_archived_as_badcase():
    badcases = BadCaseCollector()
    registry = ToolRegistry(badcases=badcases)
    registry.register(_spec("svc.restart", risk_level=RiskLevel.HIGH, required_permission="tool:high"), _echo)

    with pytest.raises(ToolPolicyDenied):
        await registry.invoke("svc.restart", {}, APPROVER, AgentTrace(), approved=True)

    assert badcases.list()[0].category is BadCaseCategory.POLICY_DENIED


# --------------------------------------------------------------- 元信息

def test_registry_describe_and_specs():
    registry = ToolRegistry()
    registry.register(_spec("a"), _echo)
    registry.register(_spec("b", cacheable=True, idempotent=True, resource_field="service"), _echo)

    assert registry.has("a") is True
    assert registry.has("z") is False
    assert registry.get("b").cacheable is True
    assert len(registry.specs()) == 2

    described = {item["name"]: item for item in registry.describe()}
    assert described["b"]["idempotent"] is True
    assert described["b"]["circuit_open"] is False


async def test_timeout_is_enforced():
    async def slow(payload):
        await asyncio.sleep(0.5)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(_spec("slow", timeout_seconds=0.05, max_retries=0), slow)

    with pytest.raises(ToolError):
        await registry.invoke("slow", {}, VIEWER, AgentTrace())
