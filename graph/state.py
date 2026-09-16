from __future__ import annotations

from typing import Any, TypedDict


class IncidentGraphState(TypedDict, total=False):
    workflow_id: str
    alarm: dict[str, Any]
    logs: list[str]
    evidence: list[dict[str, Any]]
    root_cause: str
    confidence: float
    proposed_action: str
    approved: bool
    errors: list[str]
