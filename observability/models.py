"""可观测性的数据契约：Span / Metric / Evaluation / Feedback / BadCase。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class SpanKind(StrEnum):
    """Span 类型，决定在前端调用树中如何着色与折叠。"""

    WORKFLOW = "workflow"
    ACTIVITY = "activity"
    AGENT = "agent"
    GRAPH_NODE = "graph_node"
    TOOL = "tool"
    LLM = "llm"
    GOVERNANCE = "governance"


class Span(BaseModel):
    span_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind = SpanKind.AGENT
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    duration_ms: float | None = None
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @property
    def is_root(self) -> bool:
        return self.parent_span_id is None


class MetricSample(BaseModel):
    name: str
    value: float
    unit: str = "count"
    labels: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvalResult(BaseModel):
    case_id: str
    metric: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class BadCaseCategory(StrEnum):
    """BadCase 分类。覆盖"工具报错、参数非法、输出异常、用户负反馈"四类来源。"""

    TOOL_ERROR = "tool_error"
    INVALID_PARAMETER = "invalid_parameter"
    OUTPUT_ANOMALY = "output_anomaly"
    POLICY_DENIED = "policy_denied"
    ONTOLOGY_VIOLATION = "ontology_violation"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_TIMEOUT = "approval_timeout"
    NEGATIVE_FEEDBACK = "negative_feedback"
    LLM_FALLBACK = "llm_fallback"
    RATE_LIMITED = "rate_limited"


class BadCase(BaseModel):
    """一条被捕获的失败样本，构成闭环迭代的输入。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    category: BadCaseCategory
    source: str
    """产生方：Agent 名 / 工具名 / API 路径 / "user"。"""
    message: str
    workflow_id: str | None = None
    trace_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Feedback(BaseModel):
    """用户或人工反馈。rating <= 2 时自动升级为 BadCase。"""

    trace_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = ""
    bad_case: bool = False
    workflow_id: str | None = None
    submitted_by: str = "anonymous"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_negative(self) -> bool:
        return self.bad_case or self.rating <= 2
