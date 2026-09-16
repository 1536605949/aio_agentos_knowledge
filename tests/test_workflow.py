import asyncio

import pytest

from api.app import build_service
from governance.models import Principal
from temporal.models import WorkflowStatus


async def wait_for_status(service, workflow_id, statuses, attempts=50):
    for _ in range(attempts):
        state = service.get(workflow_id)
        if state.status in statuses:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"workflow did not reach {statuses}: {service.get(workflow_id).status}")


@pytest.mark.asyncio
async def test_vertical_slice_approval_and_memory():
    service = build_service()
    state = await service.start(
        alarm={"alarm_id": "A1", "service": "checkout"},
        logs=["upstream timeout from payment"],
    )
    state = await wait_for_status(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})
    assert state.root_cause == "upstream_timeout"
    assert state.proposed_action == "restart_service"

    state = await service.approve(
        state.workflow_id,
        True,
        Principal(subject="oncall", roles={"approver"}),
    )
    assert state.status == WorkflowStatus.COMPLETED
    assert state.remediation_result["executed"] is True
    assert service.memory.list(state.workflow_id)[0].outcome == "completed"
    assert any(span.name == "tool.invoke" for span in service.trace(state.workflow_id).spans())


@pytest.mark.asyncio
async def test_rejection_path():
    service = build_service()
    state = await service.start({"alarm_id": "A2", "service": "api"}, ["OOM memory limit"])
    state = await wait_for_status(service, state.workflow_id, {WorkflowStatus.WAITING_APPROVAL})
    state = await service.approve(state.workflow_id, False, Principal(subject="oncall", roles={"approver"}))
    assert state.status == WorkflowStatus.REJECTED
    assert state.remediation_result is None


@pytest.mark.asyncio
async def test_approval_timeout_default_deny():
    service = build_service()
    service.approval_timeout_seconds = 0.01
    state = await service.start({"alarm_id": "A3", "service": "api"}, ["OOM memory limit"])
    state = await wait_for_status(service, state.workflow_id, {WorkflowStatus.TIMED_OUT}, attempts=100)
    assert state.approved is False
    assert "default deny" in state.error
