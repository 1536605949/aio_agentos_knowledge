"""Temporal SDK implementation of the outer durable workflow.

This module is imported only in production environments that install `temporalio`.
LangGraph/domain reasoning runs inside an Activity; the Workflow itself only owns
durable orchestration state and approval signals.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    from temporalio import activity, workflow
except ImportError as exc:  # pragma: no cover - optional integration
    raise RuntimeError("install the orchestration extra: pip install -e '.[orchestration]'") from exc


@activity.defn
async def reasoning_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from graph.pipeline import IncidentReasoningGraph
    from observability.trace import AgentTrace

    trace = AgentTrace(trace_id=payload.get("trace_id"))
    result = await IncidentReasoningGraph().run(
        payload["workflow_id"], trace, payload["alarm"], payload.get("logs", [])
    )
    return result


@activity.defn
async def remediation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    # Replace with a governed MCP/infra adapter. Kept side-effect-free in this template.
    return {
        "action": payload["action"],
        "service": payload.get("service", "unknown"),
        "executed": True,
        "mode": "temporal-demo",
    }


@workflow.defn
class IncidentWorkflow:
    def __init__(self) -> None:
        self._approval: bool | None = None

    @workflow.signal
    async def approval(self, approved: bool) -> None:
        self._approval = approved

    @workflow.query
    def approval_state(self) -> bool | None:
        return self._approval

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        reasoning = await workflow.execute_activity(
            reasoning_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=None,
        )
        action = reasoning["proposed_action"]
        if reasoning.get("requires_approval"):
            try:
                await workflow.wait_condition(lambda: self._approval is not None, timeout=timedelta(hours=24))
            except TimeoutError:
                return {**reasoning, "status": "timed_out", "approved": False}
            if self._approval is not True:
                return {**reasoning, "status": "rejected", "approved": False}

        remediation = await workflow.execute_activity(
            remediation_activity,
            {"action": action, "service": payload["alarm"].get("service")},
            start_to_close_timeout=timedelta(minutes=2),
        )
        return {**reasoning, "status": "completed", "approved": self._approval, "remediation_result": remediation}
