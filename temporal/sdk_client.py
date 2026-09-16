from __future__ import annotations

import os
from typing import Any
from uuid import uuid4


class TemporalSDKClient:
    """Real Temporal client adapter. Requires the `orchestration` extra."""

    def __init__(self, client):
        self._client = client

    @classmethod
    async def connect(cls, address: str | None = None, namespace: str | None = None):
        try:
            from temporalio.client import Client
        except ImportError as exc:
            raise RuntimeError("install the orchestration extra: pip install -e '.[orchestration]'") from exc
        client = await Client.connect(
            address or os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
            namespace=namespace or os.getenv("TEMPORAL_NAMESPACE", "default"),
        )
        return cls(client)

    async def start_incident(self, payload: dict[str, Any], workflow_id: str | None = None):
        from temporal.production import IncidentWorkflow
        return await self._client.start_workflow(
            IncidentWorkflow.run,
            payload,
            id=workflow_id or payload.get("workflow_id") or f"incident-{uuid4()}",
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "aio-agentos"),
        )

    async def approve(self, workflow_id: str, approved: bool) -> None:
        from temporal.production import IncidentWorkflow
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal(IncidentWorkflow.approval, approved)
