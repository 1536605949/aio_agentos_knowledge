from __future__ import annotations

from typing import Any

from temporal.workflow import InMemoryIncidentWorkflowService


class TemporalClient:
    """Local client facade used by tests/demo."""

    def __init__(self, service: InMemoryIncidentWorkflowService):
        self.service = service

    async def start(self, workflow: dict[str, Any]):
        return await self.service.start(
            alarm=workflow["alarm"],
            logs=workflow.get("logs", []),
            workflow_id=workflow.get("workflow_id"),
        )
