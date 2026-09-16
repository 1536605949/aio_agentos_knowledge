from __future__ import annotations

import os


async def run_worker() -> None:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError("install the orchestration extra: pip install -e '.[orchestration]'") from exc

    from temporal.production import IncidentWorkflow, reasoning_activity, remediation_activity

    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    worker = Worker(
        client,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "aio-agentos"),
        workflows=[IncidentWorkflow],
        activities=[reasoning_activity, remediation_activity],
    )
    await worker.run()
