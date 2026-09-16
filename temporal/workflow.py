from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from governance.models import Principal
from graph.pipeline import IncidentReasoningGraph
from memory.store import InMemoryMemoryStore
from memory.types import EpisodicMemory
from observability.trace import AgentTrace
from temporal.models import WorkflowState, WorkflowStatus
from tools.registry import ToolRegistry


class InMemoryIncidentWorkflowService:
    """Runnable Temporal-semantics reference.

    Durable workflow state owns orchestration fields; the inner graph is short-lived.
    High-risk actions stop in WAITING_APPROVAL until `approve()` supplies the signal.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        memory: InMemoryMemoryStore | None = None,
        approval_timeout_seconds: float = 24 * 3600,
    ) -> None:
        self.tools = tools
        self.memory = memory or InMemoryMemoryStore()
        self.graph = IncidentReasoningGraph()
        self.approval_timeout_seconds = approval_timeout_seconds
        self.states: dict[str, WorkflowState] = {}
        self.traces: dict[str, AgentTrace] = {}
        self._lock = asyncio.Lock()
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    async def start(self, alarm: dict[str, Any], logs: list[str] | None = None, workflow_id: str | None = None) -> WorkflowState:
        workflow_id = workflow_id or str(uuid4())
        async with self._lock:
            if workflow_id in self.states:
                return self.states[workflow_id]
            trace = AgentTrace()
            state = WorkflowState(workflow_id=workflow_id, alarm=alarm, logs=logs or [], trace_id=trace.trace_id)
            self.states[workflow_id] = state
            self.traces[workflow_id] = trace
        asyncio.create_task(self._run(workflow_id))
        return state

    async def _run(self, workflow_id: str) -> None:
        state = self.states[workflow_id]
        trace = self.traces[workflow_id]
        try:
            state.status = WorkflowStatus.RUNNING
            with trace.span("workflow.reasoning", workflow_id=workflow_id):
                result = await self.graph.run(workflow_id, trace, state.alarm, state.logs)
            state.root_cause = result["root_cause"]
            state.confidence = result["confidence"]
            state.proposed_action = result["proposed_action"]
            state.approval_required = bool(result["requires_approval"])
            state.updated_at = datetime.now(timezone.utc)

            if state.approval_required:
                state.status = WorkflowStatus.WAITING_APPROVAL
                self._timeout_tasks[workflow_id] = asyncio.create_task(self._approval_timeout(workflow_id))
                return
            await self._execute_remediation(state, trace, approved=False)
        except Exception as exc:
            state.status = WorkflowStatus.FAILED
            state.error = f"{type(exc).__name__}: {exc}"
            state.updated_at = datetime.now(timezone.utc)

    async def _approval_timeout(self, workflow_id: str) -> None:
        await asyncio.sleep(self.approval_timeout_seconds)
        state = self.states.get(workflow_id)
        if state and state.status == WorkflowStatus.WAITING_APPROVAL:
            state.status = WorkflowStatus.TIMED_OUT
            state.approved = False
            state.error = "approval timed out; default deny"
            state.updated_at = datetime.now(timezone.utc)
            self._write_episode(state, "approval_timeout")

    async def approve(self, workflow_id: str, approved: bool, principal: Principal) -> WorkflowState:
        state = self.get(workflow_id)
        if state.status != WorkflowStatus.WAITING_APPROVAL:
            raise ValueError(f"workflow is not waiting for approval: {state.status.value}")
        if "approval:decide" not in self.tools.policy.permissions_for(principal):
            raise PermissionError("principal lacks approval:decide")

        timeout_task = self._timeout_tasks.pop(workflow_id, None)
        if timeout_task:
            timeout_task.cancel()

        state.approved = approved
        if not approved:
            state.status = WorkflowStatus.REJECTED
            state.updated_at = datetime.now(timezone.utc)
            self._write_episode(state, "rejected")
            return state

        state.status = WorkflowStatus.RUNNING
        # Separation of duties: the human principal approves; a governed workflow service
        # identity executes the already-approved action.
        executor = Principal(subject="workflow-executor", roles={"admin"})
        await self._execute_remediation(state, self.traces[workflow_id], approved=True, principal=executor)
        return state

    async def _execute_remediation(
        self, state: WorkflowState, trace: AgentTrace, approved: bool, principal: Principal | None = None
    ) -> None:
        if state.proposed_action == "restart_service":
            principal = principal or Principal(subject="workflow", roles={"admin"})
            result = await self.tools.invoke(
                "service.restart",
                {"service": state.alarm.get("service", "unknown"), "workflow_id": state.workflow_id},
                principal=principal,
                trace=trace,
                approved=approved,
            )
            state.remediation_result = result
        else:
            state.remediation_result = {"action": state.proposed_action, "executed": False}
        state.status = WorkflowStatus.COMPLETED
        state.updated_at = datetime.now(timezone.utc)
        self._write_episode(state, "completed")

    def _write_episode(self, state: WorkflowState, outcome: str) -> None:
        self.memory.put(EpisodicMemory(
            workflow_id=state.workflow_id,
            content={
                "alarm": state.alarm,
                "root_cause": state.root_cause,
                "proposed_action": state.proposed_action,
                "remediation_result": state.remediation_result,
            },
            outcome=outcome,
        ))

    def get(self, workflow_id: str) -> WorkflowState:
        try:
            return self.states[workflow_id]
        except KeyError as exc:
            raise KeyError(f"workflow not found: {workflow_id}") from exc

    def trace(self, workflow_id: str) -> AgentTrace:
        self.get(workflow_id)
        return self.traces[workflow_id]
