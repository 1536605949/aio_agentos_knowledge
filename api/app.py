"""FastAPI 应用：对外契约层。

端点分组：

- **故障处置**：``POST /alarm``、``GET /workflow/{id}``、``GET /workflows``、``POST /approval/{id}``
- **可观测**：``GET /workflow/{id}/trace``、``GET /workflow/{id}/trace/tree``、``GET /metrics``
- **自描述**：``GET /healthz``、``GET /ontology``、``GET /skills``、``GET /agents``、
  ``GET /tools``、``GET /policies``、``GET /router/route``、``GET /router/plan``
- **闭环**：``POST /feedback``、``GET /badcases``
- **协议**：``GET /.well-known/agent-card.json``、``GET /mcp/tools``

鉴权策略（原实现的一个隐患已修正）：

- 配置了 ``AIO_AGENTOS_API_KEY`` 时**必须**携带 ``X-API-Key``；
- 配置了 ``AIO_AGENTOS_APPROVER_KEY`` 时审批端点**必须**额外携带 ``X-Approver-Key``；
- 两者都未配置时仍可访问（本地开发），但 ``/healthz`` 会把风险项列进 ``security_warnings``，
  而不是像原实现那样静默放行。

审批主体固定为 ``approver`` 角色——它具备 ``approval:decide`` 但**不具备** ``tool:high``，
因此审批者无法自己执行高风险动作（职责分离）。
"""

from __future__ import annotations

from math import ceil

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status

from api.schemas import (
    AlarmRequest,
    ApprovalRequest,
    BadCaseResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    RouteResponse,
    WorkflowResponse,
)
from bootstrap import Runtime, build_runtime, build_workflow_service
from config import get_settings
from governance.models import Principal
from observability.models import BadCaseCategory, Feedback
from protocol.catalog import build_agent_card, build_mcp_catalog
from temporal.workflow import IncidentWorkflowService

runtime: Runtime = build_runtime()
service: IncidentWorkflowService = build_workflow_service(runtime)

app = FastAPI(
    title="AIO-AgentOS",
    version="3.5.0",
    description="AIOps 故障根因分析与受治理修复的多 Agent 运行时（参考实现）",
)


def build_service() -> IncidentWorkflowService:
    """构造一个使用**全新运行时**的服务实例（测试隔离用）。"""
    return build_workflow_service(build_runtime())


# ------------------------------------------------------------------ 依赖

def _require_api_key(x_api_key: str | None) -> None:
    expected = runtime.settings.api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")


def _require_approver_key(x_approver_key: str | None) -> None:
    expected = runtime.settings.approver_key
    if expected and x_approver_key != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid or missing approver credential")


async def rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """按调用方隔离的令牌桶限流。仅作用于写端点，避免读端点被轮询打爆。"""
    caller = x_api_key or (request.client.host if request.client else "anonymous")
    verdict = await runtime.limiter.acquire(f"api:{caller}")
    if verdict.allowed:
        return
    runtime.badcases.capture(
        BadCaseCategory.RATE_LIMITED,
        source=f"api:{caller}",
        message=verdict.reason,
        detail={"path": str(request.url.path)},
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="rate limit exceeded",
        headers={"Retry-After": str(max(1, ceil(verdict.retry_after_seconds)))},
    )


def _response(state) -> WorkflowResponse:
    return WorkflowResponse.model_validate(state.model_dump())


# -------------------------------------------------------------- 故障处置

@app.post(
    "/alarm",
    response_model=WorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["incident"],
    summary="启动一次故障处置",
)
async def create_alarm(
    req: AlarmRequest,
    x_api_key: str | None = Header(default=None),
    _: None = Depends(rate_limit),
) -> WorkflowResponse:
    _require_api_key(x_api_key)
    key = req.idempotency_key or req.alarm_id
    existing = service.resolve_idempotency(key)
    if existing is not None:
        return _response(existing)
    alarm = req.model_dump(exclude={"logs", "idempotency_key", "intent", "topology"})
    state = await service.start(
        alarm=alarm,
        logs=req.logs,
        intent=req.intent,
        idempotency_key=key,
        topology=req.topology,
    )
    return _response(state)


@app.get("/workflow/{workflow_id}", response_model=WorkflowResponse, tags=["incident"])
async def get_workflow(workflow_id: str, x_api_key: str | None = Header(default=None)) -> WorkflowResponse:
    _require_api_key(x_api_key)
    try:
        return _response(service.get(workflow_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc


@app.get("/workflows", response_model=list[WorkflowResponse], tags=["incident"])
async def list_workflows(
    limit: int = Query(default=50, ge=1, le=500),
    x_api_key: str | None = Header(default=None),
) -> list[WorkflowResponse]:
    _require_api_key(x_api_key)
    return [_response(state) for state in service.list_workflows(limit=limit)]


@app.post("/approval/{workflow_id}", response_model=WorkflowResponse, tags=["incident"])
async def approve_workflow(
    workflow_id: str,
    req: ApprovalRequest,
    x_api_key: str | None = Header(default=None),
    x_approver_key: str | None = Header(default=None),
    _: None = Depends(rate_limit),
) -> WorkflowResponse:
    _require_api_key(x_api_key)
    _require_approver_key(x_approver_key)
    try:
        state = await service.approve(
            workflow_id,
            approved=req.approved,
            principal=Principal(subject=req.approver, roles={"approver"}),
        )
        return _response(state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------- 可观测

@app.get("/workflow/{workflow_id}/trace", tags=["observability"])
async def get_trace(workflow_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    try:
        trace = service.trace(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    return trace.to_dict()


@app.get("/workflow/{workflow_id}/trace/tree", tags=["observability"])
async def get_trace_tree(workflow_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    """返回**嵌套**调用树（父子层级），便于前端直接渲染。"""
    _require_api_key(x_api_key)
    try:
        trace = service.trace(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    return {"trace_id": trace.trace_id, "summary": trace.summary(), "tree": trace.tree()}


@app.get("/metrics", tags=["observability"])
async def get_metrics(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    snapshot = runtime.metrics.snapshot()
    cache_stats = await runtime.cache.stats()
    return {
        "counters": snapshot["counters"],
        "gauges": snapshot["gauges"],
        "latency_ms": snapshot["latency_ms"],
        "llm": runtime.llm.stats(),
        "cache": {
            "hits": cache_stats.hits,
            "misses": cache_stats.misses,
            "evictions": cache_stats.evictions,
            "expirations": cache_stats.expirations,
            "size": cache_stats.size,
            "hit_rate": cache_stats.hit_rate,
        },
        "rate_limit": runtime.limiter.stats(),
        "resource_locks": {
            resource: {
                "acquisitions": stats.acquisitions,
                "timeouts": stats.timeouts,
                "current_holders": stats.current_holders,
                "queued": stats.queued,
            }
            for resource, stats in runtime.locks.stats().items()
        },
        "tools": runtime.tools.stats(),
        "badcases": runtime.badcases.summary(),
    }


# ---------------------------------------------------------------- 自描述

@app.get("/healthz", response_model=HealthResponse, tags=["meta"])
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", **runtime.health())


@app.get("/ontology", tags=["meta"])
async def get_ontology() -> dict:
    """返回只读 TBox：版本、类、属性、关系与公理。"""
    return runtime.ontology.summary()


@app.get("/skills", tags=["meta"])
async def get_skills() -> dict:
    return {
        "capabilities": [
            {"name": spec.name, "description": spec.description}
            for spec in sorted(runtime.skills.capabilities.values(), key=lambda spec: spec.name)
        ],
        "skills": runtime.router.describe(),
        "intents": runtime.router.intents(),
    }


@app.get("/agents", tags=["meta"])
async def get_agents() -> dict:
    return {"agents": runtime.agents.describe()}


@app.get("/tools", tags=["meta"])
async def get_tools() -> dict:
    return {"tools": runtime.tools.describe()}


@app.get("/policies", tags=["meta"])
async def get_policies() -> dict:
    return {"roles": runtime.tools.policy.describe()}


@app.get("/router/route", response_model=RouteResponse, tags=["meta"])
async def route_intent(intent: str = Query(min_length=1)) -> RouteResponse:
    try:
        decision = runtime.router.route(intent)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RouteResponse(**decision.model_dump())


@app.get("/router/plan", response_model=list[RouteResponse], tags=["meta"])
async def route_plan(intent: str = Query(default="diagnose")) -> list[RouteResponse]:
    return [RouteResponse(**decision.model_dump()) for decision in runtime.router.plan(intent)]


# ------------------------------------------------------------------ 闭环

@app.post("/feedback", response_model=FeedbackResponse, tags=["feedback"])
async def submit_feedback(
    req: FeedbackRequest,
    x_api_key: str | None = Header(default=None),
    _: None = Depends(rate_limit),
) -> FeedbackResponse:
    _require_api_key(x_api_key)
    feedback = Feedback(
        trace_id=req.trace_id,
        rating=req.rating,
        comment=req.comment,
        bad_case=req.bad_case,
        workflow_id=req.workflow_id,
        submitted_by=req.submitted_by,
    )
    case = runtime.badcases.from_feedback(feedback)
    if case is None:
        return FeedbackResponse(accepted=True, message="feedback recorded; not a bad case")
    return FeedbackResponse(accepted=True, bad_case_id=case.id, message="feedback escalated to bad case")


@app.get("/badcases", response_model=list[BadCaseResponse], tags=["feedback"])
async def list_badcases(
    limit: int = Query(default=50, ge=1, le=500),
    category: BadCaseCategory | None = None,
    unresolved_only: bool = False,
    x_api_key: str | None = Header(default=None),
) -> list[BadCaseResponse]:
    _require_api_key(x_api_key)
    cases = runtime.badcases.list(limit=limit, category=category, unresolved_only=unresolved_only)
    return [BadCaseResponse.model_validate(case.model_dump()) for case in cases]


@app.get("/badcases/summary", tags=["feedback"])
async def badcase_summary(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    return runtime.badcases.summary()


# ------------------------------------------------------------------ 协议

@app.get("/.well-known/agent-card.json", tags=["protocol"])
async def agent_card() -> dict:
    """A2A 能力发现端点。"""
    card = build_agent_card(runtime.agents, runtime.skills, runtime.settings)
    return card.model_dump()


@app.get("/mcp/tools", tags=["protocol"])
async def mcp_tools() -> dict:
    """出站 MCP 工具目录。"""
    catalog = build_mcp_catalog(runtime.tools)
    return {"tools": [descriptor.model_dump() for descriptor in catalog.list_tools()]}


__all__ = ["app", "build_service", "runtime", "service"]


def _settings_summary() -> dict:
    """便于脚本打印当前生效配置（不含任何密钥值）。"""
    settings = get_settings()
    return {
        "environment": settings.environment.value,
        "llm_provider": settings.llm_provider,
        "store_backend": settings.store_backend,
        "auth_configured": settings.auth_configured,
    }
