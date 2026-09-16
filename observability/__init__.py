"""可观测性层。

契约：本层**只依赖标准库与 persistence**（向下依赖基础设施），
不被 ontology / memory / runtime / governance / tools / agents / graph / temporal / api 反向依赖，
因此它可以被任何一层安全引用。

对外暴露：
- :class:`AgentTrace` —— 带父子层级的 Span 采集器
- :class:`MetricsCollector` —— 计数器 / 仪表 / 时长分布
- :class:`BadCaseCollector` —— 失败样本闭环入口
- 数据契约：``Span`` / ``SpanKind`` / ``MetricSample`` / ``EvalResult`` / ``Feedback`` / ``BadCase``
"""

from observability.badcase import BadCaseCollector
from observability.metrics import MetricsCollector
from observability.models import (
    BadCase,
    BadCaseCategory,
    EvalResult,
    Feedback,
    MetricSample,
    Span,
    SpanKind,
    SpanStatus,
)
from observability.trace import AgentTrace

__all__ = [
    "AgentTrace",
    "BadCase",
    "BadCaseCategory",
    "BadCaseCollector",
    "EvalResult",
    "Feedback",
    "MetricSample",
    "MetricsCollector",
    "Span",
    "SpanKind",
    "SpanStatus",
]
