from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class WorkflowState(BaseModel):
    """Durable source of truth for incident orchestration."""

    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    alarm: dict[str, Any]
    logs: list[str] = Field(default_factory=list)
    trace_id: str
    root_cause: str | None = None
    confidence: float | None = None
    proposed_action: str | None = None
    approval_required: bool = False
    approved: bool | None = None
    remediation_result: dict[str, Any] | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
