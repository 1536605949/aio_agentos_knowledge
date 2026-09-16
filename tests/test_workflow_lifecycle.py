"""工作流生命周期测试：状态机、本体门禁、持久化恢复、职责分离与超时。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from concurrency import ResourceLockManager
from governance.models import Principal
from graph.pipeline import IncidentReasoningGraph
from observability import BadCaseCategory, BadCaseCollector
from persistence import InMemoryDocumentStore
from temporal import (
    ALLOWED_TRANSITIONS,
    IncidentWorkflowService,
    WorkflowStatus,
    assert_transition,
)
from temporal.models import TERMINAL_STATUSES
from tools import build_default_tool_registry

APPROVER = Principal(subject="oncall", roles={"approver"})
NOBODY = Principal(subject="intern", roles={"viewer"})


_SERVICES: list[IncidentWorkflowService] = []


@pytest.fixture(autouse=True)
async def _close_services():
    """用例结束后关闭服务，取消挂起的审批超时任务。"""
    _SERVICES.clear()
    yield
    for service in _SERVICES:
        await service.close()
    _SERVICES.clear()


def _service(**kwargs: Any) -> IncidentWorkflowService:
    tools = kwargs.pop("tools", None) or build_default_tool_registry()
    kwargs.setdefault("badcases", BadCaseCollector())
    service = IncidentWorkflowService(tools, **kwargs)
    _SERVICES.append(service)
    return service


async def _wait(service: IncidentWorkflowService, workflow_id: str, statuses, attempts: int = 300):
    for _ in range(attempts):
        state = service.get(workflow_id)
        if state.status in statuses:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"stuck at {service.get(workflow_id).status}")


# ------------------------------------------------------------- 状态转移表

def test_state_transition_table_rejects_illegal_moves():
    assert_transition(WorkflowStatus.PENDING, WorkflowStatus.RUNNING)
    assert_transition(WorkflowStatus.WAITING_APPROVAL, WorkflowStatus.TIMED_OUT)
    with pytest.raises(ValueError):
        assert_transition(WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)
    with pytest.raises(ValueError):
        assert_transition(WorkflowStatus.PENDING, WorkflowStatus.COMPLETED)

    assert ALLOWED_TRANSITIONS[WorkflowStatus.COMPLETED] == frozenset()
    assert WorkflowStatus.COMPLETED in TERMINAL_STATUSES


# ------------------------------------------------------------ 正常与拒绝路径

async def test_low_risk_path_completes_without_approval():
    service = _service()
    state = await service.start({"alarm_id": "L1", "service": "api"}, ["database connection refused"])
    state = await _wait(service, state.workflow_id, TERMINAL_STATUSES)

    assert state.status is WorkflowStatus.COMPLETED
    assert state.root_cause == "database_dependency_failure"
    assert state.proposed_action == "escalate_to_dba"
    assert state.approval_required is False
    assert state.remediation_result["executed"] is True
    assert state.approved is None  # 未经过审批门


async def test_high_risk_path_waits_then_executes():
    service = _service()
    state = await service.start({"alarm_id": "H1", "service": "checkout"}, ["upstream timeout"])
    state = await _wait(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})

    assert state.approval_required is True
    assert state.remediation_result is None  # 审批前绝不执行
    assert state.approval_deadline is not None

    state = await service.approve(state.workflow_id, True, APPROVER)
    assert state.status is WorkflowStatus.COMPLETED
    assert state.approved is True
    assert state.approved_by == "oncall"


async def test_approval_requires_approval_permission():
    service = _service()
    state = await service.start({"alarm_id": "H2", "service": "checkout"}, ["upstream timeout"])
    await _wait(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})

    with pytest.raises(PermissionError) as excinfo:
        await service.approve(state.workflow_id, True, NOBODY)
    assert "approval:decide" in str(excinfo.value)
    # 拒绝越权后状态必须保持不变
    assert service.get(state.workflow_id).status is WorkflowStatus.WAITING_APPROVAL


async def test_approval_timeout_defaults_to_deny():
    service = _service(approval_timeout_seconds=0.05)
    state = await service.start({"alarm_id": "H3", "service": "checkout"}, ["OOM memory limit"])
    state = await _wait(service, state.workflow_id, {WorkflowStatus.TIMED_OUT})

    assert state.approved is False
    assert "default deny" in state.error
    assert state.remediation_result is None
    assert any(case.category is BadCaseCategory.APPROVAL_TIMEOUT for case in service.badcases.list())


async def test_approving_a_non_waiting_workflow_raises():
    service = _service()
    state = await service.start({"alarm_id": "L2", "service": "api"}, ["database connection refused"])
    await _wait(service, state.workflow_id, TERMINAL_STATUSES)
    with pytest.raises(ValueError):
        await service.approve(state.workflow_id, True, APPROVER)


# --------------------------------------------------------------- 本体门禁

class _ViolatingGraph:
    """模拟"图产出了本体不允许的结果"（例如未来某个节点绕过了收敛逻辑）。"""

    async def run(self, workflow_id, trace, alarm, logs=None, topology=None, intent="diagnose"):
        return {
            "workflow_id": workflow_id,
            "severity": "bogus",
            "root_cause": "upstream_timeout",
            "confidence": 0.9,
            "proposed_action": "restart_service",
            "action_tool": "service.restart",
            "requires_approval": True,
            "evidence": [],
            "topology": {},
            "steps": ["normalize_alarm"],
            "errors": ["severity='bogus' is not allowed"],
        }


async def test_ontology_violation_fails_the_workflow():
    service = _service(graph=_ViolatingGraph())
    state = await service.start({"alarm_id": "O1", "service": "api"}, [])
    state = await _wait(service, state.workflow_id, TERMINAL_STATUSES)

    assert state.status is WorkflowStatus.FAILED
    assert "ontology validation failed" in state.error
    assert state.ontology_errors == ["severity='bogus' is not allowed"]
    assert any(
        case.category is BadCaseCategory.ONTOLOGY_VIOLATION for case in service.badcases.list()
    )


async def test_reasoning_timeout_fails_the_workflow():
    class _SlowGraph:
        async def run(self, *args, **kwargs):
            await asyncio.sleep(5)
            return {}

    service = _service(graph=_SlowGraph())
    service.settings.reasoning_timeout_seconds = 0.05
    state = await service.start({"alarm_id": "T1", "service": "api"}, [])
    state = await _wait(service, state.workflow_id, TERMINAL_STATUSES)
    assert state.status is WorkflowStatus.FAILED


# ------------------------------------------------------------ 持久化与恢复

async def test_state_is_persisted_and_readable_by_a_fresh_service():
    store = InMemoryDocumentStore()
    badcases = BadCaseCollector(store=store)

    first = _service(store=store, badcases=badcases)
    state = await first.start({"alarm_id": "P1", "service": "checkout"}, ["upstream timeout"])
    await _wait(first, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})

    # 新进程重新装配：states/traces 都是空的，只能靠持久化恢复
    second = _service(store=store, badcases=badcases)
    recovered = second.get(state.workflow_id)
    assert recovered.status is WorkflowStatus.WAITING_APPROVAL
    assert recovered.root_cause == "upstream_timeout"

    # Trace 也已归档，可回读完整调用树
    recovered_trace = second.trace(state.workflow_id)
    assert recovered_trace.summary()["max_depth"] >= 2
    assert recovered_trace.trace_id == state.trace_id

    # 恢复后仍能完成审批闭环
    done = await second.approve(state.workflow_id, True, APPROVER)
    assert done.status is WorkflowStatus.COMPLETED
    assert done.remediation_result["executed"] is True


async def test_idempotency_key_survives_restart():
    store = InMemoryDocumentStore()
    first = _service(store=store)
    state = await first.start(
        {"alarm_id": "I1", "service": "api"}, ["database connection refused"], idempotency_key="k-1"
    )
    await _wait(first, state.workflow_id, TERMINAL_STATUSES)

    second = _service(store=store)
    replay = await second.start({"alarm_id": "I1", "service": "api"}, [], idempotency_key="k-1")
    assert replay.workflow_id == state.workflow_id


async def test_intent_is_routed_and_recorded():
    service = _service()
    state = await service.start({"alarm_id": "R1", "service": "api"}, ["upstream timeout"], intent="根因")
    assert state.intent == "根因"

    fallback = await service.start({"alarm_id": "R2", "service": "api"}, [], intent="不存在的意图")
    assert fallback.intent == "diagnose"


# ------------------------------------------------------------- 资源与配额

async def test_service_resource_lock_serialises_remediation():
    locks = ResourceLockManager(default_timeout_seconds=0.05)
    tools = build_default_tool_registry(locks=locks)
    service = _service(tools=tools, locks=locks)

    # 先占住 checkout 的资源锁
    async with locks.acquire("service:checkout", holder="other"):
        state = await service.start({"alarm_id": "C1", "service": "checkout"}, ["upstream timeout"])
        await _wait(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})
        state = await service.approve(state.workflow_id, True, APPROVER)

    assert state.status is WorkflowStatus.FAILED
    assert "busy" in (state.error or "")


async def test_reasoning_rate_limit_fails_fast():
    from resilience import TokenBucketLimiter

    limiter = TokenBucketLimiter(rate_per_minute=1, burst=1)
    await limiter.acquire("workflow:reasoning")  # 预先耗尽配额

    service = _service(limiter=limiter)
    state = await service.start({"alarm_id": "Q1", "service": "api"}, ["upstream timeout"])
    state = await _wait(service, state.workflow_id, TERMINAL_STATUSES)

    assert state.status is WorkflowStatus.FAILED
    assert "rate limited" in (state.error or "")


# ------------------------------------------------------------ 记忆与指标

async def test_episodic_memory_and_metrics_are_written():
    from observability import MetricsCollector

    metrics = MetricsCollector()
    service = _service(metrics=metrics)
    state = await service.start({"alarm_id": "M1", "service": "api"}, ["database connection refused"])
    await _wait(service, state.workflow_id, TERMINAL_STATUSES)

    episodes = service.memory.list(state.workflow_id)
    assert [episode.outcome for episode in episodes] == ["completed"]
    assert episodes[0].content["root_cause"] == "database_dependency_failure"
    assert episodes[0].tags == ["completed", "diagnose"]

    counters = metrics.snapshot()["counters"]
    assert counters["aio_agentos_workflow_started{intent=diagnose}"] == 1
    assert counters["aio_agentos_workflow_finished{status=completed}"] == 1
    latency = metrics.snapshot()["latency_ms"]
    assert latency["aio_agentos_workflow_duration_ms{intent=diagnose}"]["count"] == 1


async def test_paused_workflow_is_not_counted_as_finished():
    from observability import MetricsCollector

    metrics = MetricsCollector()
    service = _service(metrics=metrics)
    state = await service.start({"alarm_id": "M2", "service": "checkout"}, ["upstream timeout"])
    await _wait(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})

    counters = metrics.snapshot()["counters"]
    assert counters.get("aio_agentos_workflow_paused{status=waiting_approval}") == 1
    assert "aio_agentos_workflow_finished{status=waiting_approval}" not in counters


async def test_graph_result_carries_steps_and_topology():
    """内层图必须真的跑完全部节点，而不是提前返回。"""
    from observability import AgentTrace

    tools = build_default_tool_registry()
    graph = IncidentReasoningGraph(tools=tools)
    result = await graph.run(
        "w-1", AgentTrace("t-1"), {"alarm_id": "G1", "service": "checkout"}, ["upstream timeout"]
    )
    assert result["steps"] == [
        "normalize_alarm",
        "collect_topology",
        "collect_logs",
        "reason",
        "propose",
        "supervise",
    ]
    assert result["topology"]["dependencies"] == ["payment", "inventory", "cart"]
    assert result["errors"] == []
    assert result["severity"] == "unknown"
