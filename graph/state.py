"""内层推理图的共享状态。

这是一个 ``TypedDict``，只承载**单次推理运行**的中间结果。
跨进程、跨重启的编排字段属于 :class:`temporal.models.WorkflowState`，
两者不要混用（见 ``docs/state-ownership.md``）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class IncidentGraphState(TypedDict, total=False):
    workflow_id: str
    intent: str
    alarm: dict[str, Any]
    normalized_alarm: dict[str, Any]
    severity: str
    logs: list[str]
    topology: dict[str, Any]
    evidence: list[dict[str, Any]]
    root_cause: str
    confidence: float
    rationale: str
    evidence_refs: list[str]
    proposed_action: str
    action_tool: str | None
    action_rationale: str
    requires_approval: bool
    approved: bool
    steps: list[str]
    errors: list[str]
    ontology_violation: bool


__all__ = ["IncidentGraphState"]
