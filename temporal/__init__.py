"""外层持久化编排层。

- :class:`IncidentWorkflowService` —— 不依赖 Temporal Server 的可运行参考实现
- :mod:`temporal.production` —— 真正的 Temporal SDK 版本（可选依赖）
"""

from temporal.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    WorkflowState,
    WorkflowStatus,
    assert_transition,
)
from temporal.workflow import (
    IncidentWorkflowService,
    InMemoryIncidentWorkflowService,
    build_service,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "IncidentWorkflowService",
    "InMemoryIncidentWorkflowService",
    "WorkflowState",
    "WorkflowStatus",
    "assert_transition",
    "build_service",
]
