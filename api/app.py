from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status

from api.schemas import AlarmRequest, ApprovalRequest, WorkflowResponse
from governance.models import Principal, RiskLevel
from governance.policy import PolicyEngine
from memory.store import InMemoryMemoryStore
from temporal.workflow import InMemoryIncidentWorkflowService
from tools.models import ToolSpec
from tools.registry import ToolRegistry


async def _restart_service(payload: dict[str, Any]) -> dict[str, Any]:
    # Safe demo activity. Production replaces this handler with a real MCP/infra adapter.
    return {"action": "restart_service", "service": payload["service"], "executed": True, "mode": "demo"}


def build_service() -> InMemoryIncidentWorkflowService:
    registry = ToolRegistry(policy=PolicyEngine())
    registry.register(
        ToolSpec(
            name="service.restart",
            description="Restart an unhealthy service",
            risk_level=RiskLevel.HIGH,
            timeout_seconds=5,
            max_retries=1,
            required_permission="tool:high",
        ),
        _restart_service,
    )
    return InMemoryIncidentWorkflowService(registry, InMemoryMemoryStore())


service = build_service()
app = FastAPI(title="AIO-AgentOS", version="3.5.0")
_idempotency: dict[str, str] = {}


def _check_api_key(x_api_key: str | None) -> None:
    configured = os.getenv("AIO_AGENTOS_API_KEY")
    if configured and x_api_key != configured:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


def _response(state) -> WorkflowResponse:
    return WorkflowResponse.model_validate(state.model_dump())


@app.post("/alarm", response_model=WorkflowResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_alarm(req: AlarmRequest, x_api_key: str | None = Header(default=None)) -> WorkflowResponse:
    _check_api_key(x_api_key)
    key = req.idempotency_key or req.alarm_id
    if key in _idempotency:
        return _response(service.get(_idempotency[key]))
    alarm = req.model_dump(exclude={"logs", "idempotency_key"})
    state = await service.start(alarm=alarm, logs=req.logs)
    _idempotency[key] = state.workflow_id
    return _response(state)


@app.get("/workflow/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str, x_api_key: str | None = Header(default=None)) -> WorkflowResponse:
    _check_api_key(x_api_key)
    try:
        return _response(service.get(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")


@app.post("/approval/{workflow_id}", response_model=WorkflowResponse)
async def approve_workflow(
    workflow_id: str,
    req: ApprovalRequest,
    x_api_key: str | None = Header(default=None),
    x_approver_key: str | None = Header(default=None),
) -> WorkflowResponse:
    _check_api_key(x_api_key)
    configured_approver_key = os.getenv("AIO_AGENTOS_APPROVER_KEY")
    if configured_approver_key and x_approver_key != configured_approver_key:
        raise HTTPException(status_code=403, detail="invalid approver credential")
    try:
        state = await service.approve(
            workflow_id,
            approved=req.approved,
            principal=Principal(subject=req.approver, roles={"approver"}),
        )
        return _response(state)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/workflow/{workflow_id}/trace")
async def get_trace(workflow_id: str, x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    try:
        return {"trace_id": service.trace(workflow_id).trace_id, "spans": [s.model_dump(mode="json") for s in service.trace(workflow_id).spans()]}
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")
