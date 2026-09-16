"""外层持久化工作流服务。

这是 **Temporal 语义的可运行参考实现**（不依赖 Temporal Server）：

- ``WorkflowState`` 是持久化编排字段的唯一事实源，每次状态迁移都落 :class:`DocumentStore`。
- 高风险动作停在 ``WAITING_APPROVAL``，直到 ``approve()`` 提供信号；超时**默认拒绝**。
- 职责分离：人工主体批准，独立的 ``workflow-executor`` 身份执行。
- 推理图是短生命周期内层，做完就返回，不持有跨重启状态。
- 幂等：``idempotency_key`` 落库，进程重启后重复提交仍返回同一 workflow。

与 Temporal 真实实现的对应关系见 ``temporal/production.py``。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from concurrency.locks import ResourceLockManager, ResourceLockTimeout
from config import Settings, get_settings
from governance.models import Principal
from graph.pipeline import IncidentReasoningGraph
from memory.store import InMemoryMemoryStore
from memory.types import EpisodicMemory
from observability.badcase import BadCaseCollector
from observability.metrics import MetricsCollector
from observability.models import BadCaseCategory, SpanKind
from observability.trace import AgentTrace
from ontology.domain import build_ontology
from ontology.models import OntologyRegistry
from persistence.base import DocumentStore, InMemoryDocumentStore
from resilience.ratelimit import TokenBucketLimiter
from router.router import RouteSkillRouter
from skills.catalog import build_default_skill_registry
from temporal.models import WorkflowState, WorkflowStatus, assert_transition
from tools.registry import ToolError, ToolRegistry

WORKFLOW_COLLECTION = "workflows"
IDEMPOTENCY_COLLECTION = "workflow_idempotency"
TRACE_COLLECTION = "traces"


class IncidentWorkflowService:
    """故障处置工作流服务。"""

    def __init__(
        self,
        tools: ToolRegistry,
        memory: InMemoryMemoryStore | None = None,
        approval_timeout_seconds: float | None = None,
        *,
        settings: Settings | None = None,
        store: DocumentStore | None = None,
        locks: ResourceLockManager | None = None,
        limiter: TokenBucketLimiter | None = None,
        badcases: BadCaseCollector | None = None,
        metrics: MetricsCollector | None = None,
        ontology: OntologyRegistry | None = None,
        router: RouteSkillRouter | None = None,
        graph: IncidentReasoningGraph | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.tools = tools
        self.memory = memory or InMemoryMemoryStore()
        self.store = store or InMemoryDocumentStore()
        self.locks = locks or ResourceLockManager()
        self.limiter = limiter
        self.badcases = badcases
        self.metrics = metrics
        self.ontology = ontology or build_ontology()
        self.router = router or RouteSkillRouter(build_default_skill_registry())
        self.graph = graph or IncidentReasoningGraph(
            tools=tools, ontology=self.ontology, settings=self.settings
        )
        self.approval_timeout_seconds = (
            self.settings.approval_timeout_seconds
            if approval_timeout_seconds is None
            else approval_timeout_seconds
        )
        self.states: dict[str, WorkflowState] = {}
        self.traces: dict[str, AgentTrace] = {}
        self._lock = asyncio.Lock()
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ 启动

    async def start(
        self,
        alarm: dict[str, Any],
        logs: list[str] | None = None,
        workflow_id: str | None = None,
        *,
        intent: str | None = None,
        idempotency_key: str | None = None,
        topology: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """启动一次故障处置。``idempotency_key`` 相同则返回既有工作流。"""
        workflow_id = workflow_id or str(uuid4())
        async with self._lock:
            if idempotency_key:
                existing = self.resolve_idempotency(idempotency_key)
                if existing is not None:
                    return existing
            if workflow_id in self.states:
                return self.states[workflow_id]

            decision = self.router.route_or_default(intent)
            trace = AgentTrace()
            state = WorkflowState(
                workflow_id=workflow_id,
                alarm=alarm,
                logs=list(logs or []),
                trace_id=trace.trace_id,
                intent=decision.intent,
                idempotency_key=idempotency_key,
                topology=topology or {},
            )
            self.states[workflow_id] = state
            self.traces[workflow_id] = trace
            self._persist(state)
            if idempotency_key:
                self.store.save(IDEMPOTENCY_COLLECTION, idempotency_key, {"workflow_id": workflow_id})

        if self.metrics is not None:
            self.metrics.increment("workflow_started", intent=state.intent)
        asyncio.create_task(self._run(workflow_id))
        return state

    async def _run(self, workflow_id: str) -> None:
        state = self.states[workflow_id]
        trace = self.traces[workflow_id]
        started = datetime.now(UTC)
        try:
            self._transition(state, WorkflowStatus.RUNNING)

            # 出站限流：保护推理资源（LLM 配额）
            if self.limiter is not None:
                verdict = await self.limiter.acquire("workflow:reasoning")
                if not verdict.allowed:
                    self._capture(BadCaseCategory.RATE_LIMITED, "workflow", verdict.reason, state)
                    raise RuntimeError(f"reasoning rate limited; retry after {verdict.retry_after_seconds}s")

            with trace.span("workflow.run", kind=SpanKind.WORKFLOW, workflow_id=workflow_id, intent=state.intent):
                result = await asyncio.wait_for(
                    self.graph.run(
                        workflow_id,
                        trace,
                        state.alarm,
                        state.logs,
                        topology=state.topology,
                        intent=state.intent,
                    ),
                    timeout=self.settings.reasoning_timeout_seconds,
                )

            self._apply_result(state, result)

            if state.ontology_errors:
                self._capture(
                    BadCaseCategory.ONTOLOGY_VIOLATION,
                    "supervisor",
                    "; ".join(state.ontology_errors),
                    state,
                )
                raise RuntimeError(f"ontology validation failed: {state.ontology_errors}")

            if state.approval_required:
                state.approval_deadline = datetime.now(UTC) + timedelta(seconds=self.approval_timeout_seconds)
                self._transition(state, WorkflowStatus.WAITING_APPROVAL)
                self._timeout_tasks[workflow_id] = asyncio.create_task(self._approval_timeout(workflow_id))
                return

            await self._execute_remediation(state, trace, approved=False)
        except Exception as exc:
            self._fail(state, exc)
        finally:
            self._record_duration(state, started)

    # ------------------------------------------------------------------ 审批

    async def _approval_timeout(self, workflow_id: str) -> None:
        await asyncio.sleep(self.approval_timeout_seconds)
        state = self.states.get(workflow_id)
        if state is None or state.status is not WorkflowStatus.WAITING_APPROVAL:
            return
        state.approved = False
        state.error = "approval timed out; default deny"
        self._transition(state, WorkflowStatus.TIMED_OUT)
        self._capture(BadCaseCategory.APPROVAL_TIMEOUT, "approval", state.error, state)
        self._write_episode(state, "approval_timeout")

    async def approve(self, workflow_id: str, approved: bool, principal: Principal) -> WorkflowState:
        """提交审批决定。只有 ``approval:decide`` 权限的主体可以调用。"""
        state = self.get(workflow_id)
        if state.status is not WorkflowStatus.WAITING_APPROVAL:
            raise ValueError(f"workflow is not waiting for approval: {state.status.value}")

        decision = self.tools.policy.evaluate_approval(principal)
        if not decision.allowed:
            raise PermissionError(decision.reason)

        timeout_task = self._timeout_tasks.pop(workflow_id, None)
        if timeout_task is not None:
            timeout_task.cancel()

        state.approved = approved
        if not approved:
            state.rejected_by = principal.subject
            self._transition(state, WorkflowStatus.REJECTED)
            self._capture(
                BadCaseCategory.APPROVAL_REJECTED,
                "approval",
                f"rejected by {principal.subject}",
                state,
            )
            self._write_episode(state, "rejected")
            return state

        state.approved_by = principal.subject
        self._transition(state, WorkflowStatus.RUNNING)
        # 职责分离：人工主体负责批准，受治理的 workflow-executor 身份负责执行。
        executor = Principal(subject="workflow-executor", roles={"admin"})
        try:
            await self._execute_remediation(state, self._trace_for(state), approved=True, principal=executor)
        except Exception as exc:
            self._fail(state, exc)
        return state

    # -------------------------------------------------------------- 执行修复

    async def _execute_remediation(
        self,
        state: WorkflowState,
        trace: AgentTrace,
        approved: bool,
        principal: Principal | None = None,
    ) -> None:
        tool = state.action_tool
        if tool is None or not self.tools.has(tool):
            state.remediation_result = {
                "action": state.proposed_action,
                "executed": False,
                "reason": "no governed tool bound to this action",
            }
            self._transition(state, WorkflowStatus.COMPLETED)
            self._write_episode(state, "completed")
            return

        executor = principal or Principal(subject="workflow-executor", roles={"admin"})
        payload = {
            "service": state.alarm.get("service", "unknown"),
            "workflow_id": state.workflow_id,
            "severity": state.severity or "unknown",
        }
        # 资源锁：同一服务的修复动作串行执行，避免并发重启互相干扰。
        try:
            async with self.locks.acquire(f"service:{payload['service']}", holder="workflow"):
                with trace.span(
                    "workflow.remediation",
                    kind=SpanKind.ACTIVITY,
                    workflow_id=state.workflow_id,
                    tool=tool,
                    approved=approved,
                ):
                    state.remediation_result = await self.tools.invoke(
                        tool,
                        payload,
                        principal=executor,
                        trace=trace,
                        approved=approved,
                        idempotency_key=f"{state.workflow_id}:{tool}",
                    )
        except ResourceLockTimeout as exc:
            raise ToolError(f"service {payload['service']!r} is busy: {exc}") from exc

        self._transition(state, WorkflowStatus.COMPLETED)
        self._write_episode(state, "completed")

    # ------------------------------------------------------------------ 查询

    def get(self, workflow_id: str) -> WorkflowState:
        state = self.states.get(workflow_id)
        if state is not None:
            return state
        record = self.store.load(WORKFLOW_COLLECTION, workflow_id)
        if record is None:
            raise KeyError(f"workflow not found: {workflow_id}")
        return WorkflowState.model_validate(record)

    def resolve_idempotency(self, key: str) -> WorkflowState | None:
        record = self.store.load(IDEMPOTENCY_COLLECTION, key)
        if not record:
            return None
        workflow_id = record.get("workflow_id")
        if not workflow_id:
            return None
        try:
            return self.get(workflow_id)
        except KeyError:
            return None

    def list_workflows(self, limit: int | None = None) -> list[WorkflowState]:
        records = self.store.query(WORKFLOW_COLLECTION, limit=limit)
        return [WorkflowState.model_validate(record) for record in records]

    def trace(self, workflow_id: str) -> AgentTrace:
        """返回该工作流的 Trace。进程内没有时从持久化归档回读。"""
        state = self.get(workflow_id)
        trace = self.traces.get(workflow_id)
        if trace is not None:
            return trace
        record = self.store.load(TRACE_COLLECTION, workflow_id)
        if record is None:
            trace = AgentTrace(trace_id=state.trace_id)
            self.traces[workflow_id] = trace
            return trace
        trace = AgentTrace.from_dict(record)
        self.traces[workflow_id] = trace
        return trace

    async def close(self) -> None:
        """优雅停机：取消所有挂起的审批超时任务，避免事件循环退出时报 pending task。"""
        for task in list(self._timeout_tasks.values()):
            task.cancel()
        self._timeout_tasks.clear()

    def _trace_for(self, state: WorkflowState) -> AgentTrace:
        """取（或按 trace_id 重建）该工作流的 Trace，保证跨进程恢复后仍可继续采集。"""
        trace = self.traces.get(state.workflow_id)
        if trace is None:
            trace = AgentTrace(trace_id=state.trace_id)
            self.traces[state.workflow_id] = trace
        return trace

    # ---------------------------------------------------------------- 内部实现

    def _transition(self, state: WorkflowState, target: WorkflowStatus) -> None:
        assert_transition(state.status, target)
        state.status = target
        state.updated_at = datetime.now(UTC)
        self._persist(state)
        if self.metrics is not None:
            self.metrics.increment("workflow_status", status=target.value)

    def _apply_result(self, state: WorkflowState, result: dict[str, Any]) -> None:
        state.severity = result.get("severity")
        state.topology = result.get("topology") or {}
        state.evidence = result.get("evidence") or []
        state.root_cause = result.get("root_cause")
        state.confidence = result.get("confidence")
        state.proposed_action = result.get("proposed_action")
        state.action_tool = result.get("action_tool")
        state.approval_required = bool(result.get("requires_approval"))
        state.ontology_errors = list(result.get("errors") or [])
        state.steps = list(result.get("steps") or [])

    def _fail(self, state: WorkflowState, exc: Exception) -> None:
        state.error = f"{type(exc).__name__}: {exc}"
        try:
            self._transition(state, WorkflowStatus.FAILED)
        except ValueError:
            state.status = WorkflowStatus.FAILED
            state.updated_at = datetime.now(UTC)
            self._persist(state)
        self._capture(BadCaseCategory.TOOL_ERROR, "workflow", state.error, state)
        self._write_episode(state, "failed")
        if self.metrics is not None:
            self.metrics.increment("workflow_failed")

    def _record_duration(self, state: WorkflowState, started: datetime) -> None:
        trace = self._trace_for(state)
        state.trace_summary = trace.summary()
        state.updated_at = datetime.now(UTC)
        # 归档 Trace，使重启后仍能回读完整调用树
        self.store.save(TRACE_COLLECTION, state.workflow_id, trace.to_dict())
        self._persist(state)
        if self.metrics is None:
            return
        elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        self.metrics.observe("workflow_duration_ms", elapsed_ms, intent=state.intent)
        if state.is_terminal:
            self.metrics.increment("workflow_finished", status=state.status.value)
        else:
            # 挂起等待审批不算"结束"，单独计数，避免看板把暂停误读为完成
            self.metrics.increment("workflow_paused", status=state.status.value)

    def _capture(
        self,
        category: BadCaseCategory,
        source: str,
        message: str,
        state: WorkflowState,
    ) -> None:
        if self.badcases is None:
            return
        case = self.badcases.capture(
            category,
            source=source,
            message=message,
            workflow_id=state.workflow_id,
            trace_id=state.trace_id,
        )
        state.badcase_ids.append(case.id)
        self._persist(state)

    def _persist(self, state: WorkflowState) -> None:
        """状态与 Trace 一起落库——这样重启后既能读状态，也能读完整调用树。"""
        self.store.save(WORKFLOW_COLLECTION, state.workflow_id, state.model_dump(mode="json"))
        trace = self.traces.get(state.workflow_id)
        if trace is not None:
            self.store.save(TRACE_COLLECTION, state.workflow_id, trace.to_dict())

    def _write_episode(self, state: WorkflowState, outcome: str) -> None:
        self.memory.put(
            EpisodicMemory(
                workflow_id=state.workflow_id,
                content={
                    "intent": state.intent,
                    "alarm": state.alarm,
                    "severity": state.severity,
                    "root_cause": state.root_cause,
                    "confidence": state.confidence,
                    "proposed_action": state.proposed_action,
                    "remediation_result": state.remediation_result,
                    "ontology_errors": state.ontology_errors,
                },
                outcome=outcome,
                tags=[outcome, state.intent],
            )
        )


#: 向后兼容别名：服务不依赖任何外部进程，早期版本叫这个名字。
InMemoryIncidentWorkflowService = IncidentWorkflowService


def build_service(tools: ToolRegistry, **kwargs: Any) -> IncidentWorkflowService:
    """便捷构造（用于测试与脚本）。"""
    return IncidentWorkflowService(tools, **kwargs)


__all__ = [
    "IDEMPOTENCY_COLLECTION",
    "TRACE_COLLECTION",
    "WORKFLOW_COLLECTION",
    "IncidentWorkflowService",
    "InMemoryIncidentWorkflowService",
    "build_service",
]
