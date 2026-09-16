"""本地客户端门面。

生产环境请使用 :class:`temporal.sdk_client.TemporalSDKClient`（需要 Temporal Server）。
本类用于本地参考实现与测试：直接委托给 :class:`temporal.workflow.IncidentWorkflowService`。
"""

from __future__ import annotations

from typing import Any

from governance.models import Principal
from temporal.models import WorkflowState
from temporal.workflow import IncidentWorkflowService


class TemporalClient:
    """本地客户端门面。"""

    def __init__(self, service: IncidentWorkflowService) -> None:
        self.service = service

    async def start(self, workflow: dict[str, Any]) -> WorkflowState:
        return await self.service.start(
            alarm=workflow["alarm"],
            logs=workflow.get("logs", []),
            workflow_id=workflow.get("workflow_id"),
            intent=workflow.get("intent"),
            idempotency_key=workflow.get("idempotency_key"),
            topology=workflow.get("topology"),
        )

    async def approve(self, workflow_id: str, approved: bool, approver: str = "oncall") -> WorkflowState:
        return await self.service.approve(
            workflow_id,
            approved=approved,
            principal=Principal(subject=approver, roles={"approver"}),
        )

    def status(self, workflow_id: str) -> WorkflowState:
        return self.service.get(workflow_id)


__all__ = ["TemporalClient"]
