from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from temporal.models import WorkflowStatus


class AlarmRequest(BaseModel):
    alarm_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    severity: str = "critical"
    message: str = ""
    logs: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class WorkflowResponse(BaseModel):
    workflow_id: str
    status: WorkflowStatus
    trace_id: str
    root_cause: str | None = None
    confidence: float | None = None
    proposed_action: str | None = None
    approval_required: bool = False
    approved: bool | None = None
    remediation_result: dict[str, Any] | None = None
    error: str | None = None


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str = Field(min_length=1)
    comment: str = ""


class ErrorResponse(BaseModel):
    code: str
    message: str
