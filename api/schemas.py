"""API 请求 / 响应契约。

所有出入参都是 Pydantic 模型，因此 OpenAPI 文档是**自动生成且永远与实现一致**的。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from observability.models import BadCaseCategory
from temporal.models import WorkflowStatus

# ----------------------------------------------------------------- 请求

class AlarmRequest(BaseModel):
    """启动一次故障处置。"""

    alarm_id: str = Field(min_length=1, description="告警 ID")
    service: str = Field(min_length=1, description="受影响服务名")
    severity: str = Field(default="critical", description="告警严重级别")
    message: str = Field(default="", description="告警描述")
    logs: list[str] = Field(default_factory=list, description="日志行，作为取证输入")
    topology: dict[str, Any] = Field(default_factory=dict, description="可选的拓扑上下文")
    intent: str | None = Field(default=None, description="业务意图；缺省使用默认意图 diagnose")
    idempotency_key: str | None = Field(default=None, description="幂等键，重复提交返回同一工作流")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "alarm_id": "A-1001",
                    "service": "checkout",
                    "severity": "critical",
                    "logs": ["upstream timeout while calling payment"],
                    "idempotency_key": "alarm-A-1001",
                }
            ]
        }
    }


class ApprovalRequest(BaseModel):
    """提交审批决定。"""

    approved: bool
    approver: str = Field(min_length=1, description="审批人标识")
    comment: str = ""


class FeedbackRequest(BaseModel):
    """提交用户反馈。``rating <= 2`` 会自动升级为 BadCase。"""

    trace_id: str = Field(min_length=1)
    rating: int = Field(ge=1, le=5)
    comment: str = ""
    bad_case: bool = False
    workflow_id: str | None = None
    submitted_by: str = "anonymous"


# ----------------------------------------------------------------- 响应

class WorkflowResponse(BaseModel):
    """工作流状态快照。"""

    workflow_id: str
    status: WorkflowStatus
    trace_id: str
    intent: str = "diagnose"
    severity: str | None = None
    root_cause: str | None = None
    confidence: float | None = None
    proposed_action: str | None = None
    action_tool: str | None = None
    approval_required: bool = False
    approved: bool | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    remediation_result: dict[str, Any] | None = None
    ontology_errors: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FeedbackResponse(BaseModel):
    accepted: bool
    bad_case_id: str | None = None
    message: str = ""


class BadCaseResponse(BaseModel):
    id: str
    category: BadCaseCategory
    source: str
    message: str
    workflow_id: str | None = None
    trace_id: str | None = None
    resolved: bool = False
    created_at: datetime


class RouteResponse(BaseModel):
    intent: str
    skill: str
    capability: str
    agent: str
    tools: list[str]
    workflow_template: str


class HealthResponse(BaseModel):
    status: str
    environment: str
    llm_provider: str
    store_backend: str
    ontology_version: str
    skills: int
    tools: int
    agents: int
    security_warnings: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    code: str
    message: str


__all__ = [
    "AlarmRequest",
    "ApprovalRequest",
    "BadCaseResponse",
    "ErrorResponse",
    "FeedbackRequest",
    "FeedbackResponse",
    "HealthResponse",
    "RouteResponse",
    "WorkflowResponse",
]
