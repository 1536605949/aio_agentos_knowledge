"""外层持久化工作流的状态契约。

**状态所有权**（见 ``docs/state-ownership.md``）：

- :class:`WorkflowState` 是**持久化编排字段的唯一事实源**，每次状态迁移都会落库。
- :class:`runtime.context.AgentContext` 是单次 Agent 调用的临时上下文，**不跨 Agent 复用**，
  也不进入持久化。
- :class:`graph.state.IncidentGraphState` 只存在于一次推理 Activity 内。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(StrEnum):
    """工作流状态。``WAITING_APPROVAL`` 是唯一会长时间挂起的状态。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES: frozenset[WorkflowStatus] = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
        WorkflowStatus.TIMED_OUT,
    }
)

ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.PENDING: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.FAILED}),
    WorkflowStatus.RUNNING: frozenset(
        {
            WorkflowStatus.WAITING_APPROVAL,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
        }
    ),
    WorkflowStatus.WAITING_APPROVAL: frozenset(
        {
            WorkflowStatus.RUNNING,
            WorkflowStatus.REJECTED,
            WorkflowStatus.TIMED_OUT,
            WorkflowStatus.FAILED,
        }
    ),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.REJECTED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.TIMED_OUT: frozenset(),
}
"""显式状态转移表。非法迁移直接抛错，而不是静默改写状态。"""


def assert_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid workflow transition: {current.value} -> {target.value}")


class WorkflowState(BaseModel):
    """一次故障处置的全部持久化编排字段。"""

    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    alarm: dict[str, Any]
    logs: list[str] = Field(default_factory=list)
    trace_id: str
    intent: str = "diagnose"
    idempotency_key: str | None = None

    # --- 推理结果 ---
    severity: str | None = None
    topology: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: str | None = None
    confidence: float | None = None
    proposed_action: str | None = None
    action_tool: str | None = None
    steps: list[str] = Field(default_factory=list)
    ontology_errors: list[str] = Field(default_factory=list)

    # --- 审批与执行 ---
    approval_required: bool = False
    approved: bool | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    approval_deadline: datetime | None = None
    remediation_result: dict[str, Any] | None = None

    # --- 诊断信息 ---
    error: str | None = None
    badcase_ids: list[str] = Field(default_factory=list)
    trace_summary: dict[str, Any] | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "WorkflowState",
    "WorkflowStatus",
    "assert_transition",
]
