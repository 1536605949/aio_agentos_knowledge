"""API 端点测试：自描述、可观测、闭环与协议端点，以及鉴权/限流收紧。

注意：``api.app`` 在导入时就构建了一份模块级 runtime，因此鉴权与限流的测试
通过临时修改 ``runtime`` 上的对象来构造场景（pydantic 模型默认可变）。
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app, runtime, service
from observability import BadCaseCategory
from resilience import TokenBucketLimiter
from temporal.models import WorkflowStatus


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _wait_for(workflow_id: str, statuses: set[WorkflowStatus], attempts: int = 200):
    for _ in range(attempts):
        state = service.get(workflow_id)
        if state.status in statuses:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"workflow stuck at {service.get(workflow_id).status}")


# ------------------------------------------------------------- 自描述端点

async def test_healthz_reports_runtime_facts(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "deterministic"
    assert body["ontology_version"] == "3.5.0"
    assert body["tools"] >= 9
    assert body["agents"] == 6
    assert body["skills"] == 6
    # 未配置密钥时必须显式告警，而不是静默放行
    assert any("AIO_AGENTOS_API_KEY" in warning for warning in body["security_warnings"])


async def test_ontology_endpoint_exposes_tbox(client):
    body = (await client.get("/ontology")).json()
    assert body["version"] == "3.5.0"
    assert body["counts"] == {"classes": 10, "properties": 8, "relations": 6, "axioms": 4}
    assert "root_cause_in_vocabulary" in [axiom["name"] for axiom in body["axioms"]]


async def test_skills_endpoint_lists_capabilities_and_intents(client):
    body = (await client.get("/skills")).json()
    assert len(body["capabilities"]) == 6
    assert len(body["skills"]) == 6
    assert "diagnose" in body["intents"]


async def test_agents_endpoint_lists_specs(client):
    body = (await client.get("/agents")).json()
    names = {agent["name"] for agent in body["agents"]}
    assert names == {"alarm", "topology", "log", "reasoning", "remediation", "ticket"}
    reasoning = next(agent for agent in body["agents"] if agent["name"] == "reasoning")
    assert "root_cause_reasoning" in reasoning["capabilities"]


async def test_tools_endpoint_exposes_risk_levels(client):
    body = (await client.get("/tools")).json()
    by_name = {tool["name"]: tool for tool in body["tools"]}
    assert by_name["service.restart"]["risk_level"] == 3
    assert by_name["service.restart"]["idempotent"] is True
    assert by_name["topology.lookup"]["cacheable"] is True
    assert by_name["demo.flaky"]["max_retries"] == 3


async def test_policies_endpoint_exposes_rbac_matrix(client):
    body = (await client.get("/policies")).json()
    assert body["roles"]["approver"] == ["approval:decide", "tool:medium", "tool:read"]
    assert "tool:high" not in body["roles"]["approver"]


async def test_router_endpoints(client):
    decision = (await client.get("/router/route", params={"intent": "diagnose"})).json()
    assert decision["skill"] == "infer_root_cause"
    assert decision["agent"] == "reasoning"

    assert (await client.get("/router/route", params={"intent": "nope"})).status_code == 404

    plan = (await client.get("/router/plan")).json()
    assert [step["skill"] for step in plan] == [
        "normalize_alarm",
        "collect_logs",
        "infer_root_cause",
        "remediate_incident",
    ]


async def test_protocol_endpoints(client):
    card = (await client.get("/.well-known/agent-card.json")).json()
    assert card["name"] == "aio-agentos"
    assert "root_cause_reasoning" in card["capabilities"]
    assert "infer_root_cause" in card["skills"]

    catalog = (await client.get("/mcp/tools")).json()
    assert {tool["name"] for tool in catalog["tools"]} >= {"service.restart", "logs.search"}


# --------------------------------------------------------- 端到端 + 可观测

async def test_full_incident_flow_exposes_trace_tree_and_metrics(client):
    response = await client.post(
        "/alarm",
        json={
            "alarm_id": "EXT-1",
            "service": "checkout",
            "severity": "critical",
            "logs": ["upstream timeout while calling payment"],
            "idempotency_key": "ext-idem-1",
        },
    )
    assert response.status_code == 202
    workflow_id = response.json()["workflow_id"]
    assert response.json()["intent"] == "diagnose"

    state = await _wait_for(workflow_id, {WorkflowStatus.WAITING_APPROVAL})
    assert state.root_cause == "upstream_timeout"
    assert state.action_tool == "service.restart"
    assert state.severity == "critical"
    assert state.topology["dependencies"] == ["payment", "inventory", "cart"]
    assert state.steps == [
        "normalize_alarm",
        "collect_topology",
        "collect_logs",
        "reason",
        "propose",
        "supervise",
    ]

    listed = (await client.get("/workflows")).json()
    assert any(item["workflow_id"] == workflow_id for item in listed)

    # 嵌套调用树（不是扁平 span 列表）
    tree = (await client.get(f"/workflow/{workflow_id}/trace/tree")).json()
    assert tree["summary"]["max_depth"] >= 3
    root_names = {node["name"] for node in tree["tree"]}
    assert "workflow.run" in root_names
    workflow_node = next(node for node in tree["tree"] if node["name"] == "workflow.run")
    assert any(child["name"] == "graph.run" for child in workflow_node["children"])

    # 审批 -> 受治理执行
    approval = await client.post(
        f"/approval/{workflow_id}",
        json={"approved": True, "approver": "oncall"},
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "completed"
    assert approval.json()["approved_by"] == "oncall"
    assert approval.json()["remediation_result"]["executed"] is True

    metrics = (await client.get("/metrics")).json()
    assert metrics["counters"]["aio_agentos_workflow_started{intent=diagnose}"] >= 1
    assert metrics["llm"]["calls"] >= 3
    assert "cache" in metrics
    assert "resource_locks" in metrics
    assert metrics["tools"]["service.restart"]["invocations"] >= 1


async def test_high_risk_tool_is_not_executed_before_approval(client):
    response = await client.post(
        "/alarm",
        json={
            "alarm_id": "EXT-2",
            "service": "api",
            "severity": "high",
            "logs": ["OOM memory limit exceeded"],
            "idempotency_key": "ext-idem-2",
        },
    )
    workflow_id = response.json()["workflow_id"]
    state = await _wait_for(workflow_id, {WorkflowStatus.WAITING_APPROVAL})
    assert state.approval_required is True
    assert state.remediation_result is None  # 未审批前不得执行


async def test_rejection_records_badcase(client):
    response = await client.post(
        "/alarm",
        json={
            "alarm_id": "EXT-3",
            "service": "api",
            "logs": ["OOM memory limit exceeded"],
            "idempotency_key": "ext-idem-3",
        },
    )
    workflow_id = response.json()["workflow_id"]
    await _wait_for(workflow_id, {WorkflowStatus.WAITING_APPROVAL})

    before = (await client.get("/badcases/summary")).json()["total"]
    rejected = await client.post(
        f"/approval/{workflow_id}",
        json={"approved": False, "approver": "oncall", "comment": "not now"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejected_by"] == "oncall"

    after = (await client.get("/badcases/summary")).json()
    assert after["total"] == before + 1
    assert after["by_category"].get("approval_rejected", 0) >= 1


async def test_feedback_endpoint_escalates_negative_rating(client):
    good = await client.post(
        "/feedback",
        json={"trace_id": "t-good", "rating": 5, "comment": "accurate"},
    )
    assert good.json()["bad_case_id"] is None

    bad = await client.post(
        "/feedback",
        json={"trace_id": "t-bad", "rating": 1, "comment": "wrong root cause", "workflow_id": "w-x"},
    )
    assert bad.json()["accepted"] is True
    assert bad.json()["bad_case_id"] is not None

    cases = (await client.get("/badcases", params={"category": "negative_feedback"})).json()
    assert any(case["trace_id"] == "t-bad" for case in cases)


async def test_approval_on_non_waiting_workflow_conflicts(client):
    # 数据库类根因对应 escalate_to_dba（低风险），不需要审批即完成
    response = await client.post(
        "/alarm",
        json={"alarm_id": "EXT-4", "service": "api", "logs": ["database connection refused"]},
    )
    workflow_id = response.json()["workflow_id"]
    state = await _wait_for(
        workflow_id,
        {WorkflowStatus.COMPLETED, WorkflowStatus.WAITING_APPROVAL, WorkflowStatus.FAILED},
    )
    assert state.status is WorkflowStatus.COMPLETED
    assert state.approval_required is False
    assert state.remediation_result["executed"] is True

    conflict = await client.post(
        f"/approval/{workflow_id}", json={"approved": True, "approver": "oncall"}
    )
    assert conflict.status_code == 409


async def test_unknown_workflow_returns_404(client):
    assert (await client.get("/workflow/does-not-exist")).status_code == 404
    assert (await client.get("/workflow/does-not-exist/trace")).status_code == 404


# ------------------------------------------------------------- 鉴权与限流

async def test_api_key_is_enforced_when_configured(client):
    original = runtime.settings.api_key
    runtime.settings.api_key = "secret-key"
    try:
        assert (await client.get("/workflow/whatever")).status_code == 401
        assert (await client.get("/workflow/whatever", headers={"X-API-Key": "wrong"})).status_code == 401
        # 通过鉴权后才会走到"找不到工作流"
        assert (
            await client.get("/workflow/whatever", headers={"X-API-Key": "secret-key"})
        ).status_code == 404
    finally:
        runtime.settings.api_key = original


async def test_approver_key_is_enforced_when_configured(client):
    original = runtime.settings.approver_key
    runtime.settings.approver_key = "approver-secret"
    try:
        response = await client.post(
            "/approval/whatever", json={"approved": True, "approver": "oncall"}
        )
        assert response.status_code == 403
    finally:
        runtime.settings.approver_key = original


async def test_write_endpoints_are_rate_limited(client):
    original = runtime.limiter
    runtime.limiter = TokenBucketLimiter(rate_per_minute=1, burst=1)
    try:
        first = await client.post(
            "/alarm",
            json={"alarm_id": "RL-1", "service": "api", "logs": ["timeout"]},
        )
        assert first.status_code == 202

        second = await client.post(
            "/alarm",
            json={"alarm_id": "RL-2", "service": "api", "logs": ["timeout"]},
        )
        assert second.status_code == 429
        assert "Retry-After" in second.headers

        # 读端点不受写限流影响
        assert (await client.get("/healthz")).status_code == 200
    finally:
        runtime.limiter = original


async def test_rate_limited_event_is_archived(client):
    original = runtime.limiter
    runtime.limiter = TokenBucketLimiter(rate_per_minute=1, burst=1)
    try:
        await client.post("/alarm", json={"alarm_id": "RL-3", "service": "api", "logs": []})
        await client.post("/alarm", json={"alarm_id": "RL-4", "service": "api", "logs": []})
    finally:
        runtime.limiter = original

    cases = runtime.badcases.list(category=BadCaseCategory.RATE_LIMITED)
    assert cases, "限流事件应当被归档为 BadCase"
