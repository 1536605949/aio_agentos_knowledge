"""AIOps 默认工具目录。

把"动作名 -> 受治理工具"的映射固化成声明式目录，使
**修复动作是否需要人工审批由工具声明的风险等级决定**，
而不是由 Agent 里写死的 ``action == "restart_service"`` 判断。

这也是原实现的另一个隐患：审批门由启发式推导，新增高风险动作时容易被漏掉。
现在新增动作只需在 :data:`ACTION_TO_TOOL` 与工具规格里声明一次。

所有 handler 都是**无副作用的参考实现**（返回 ``mode="reference"``）。
生产环境把 handler 换成 MCP / 基础设施适配器即可，其余链路不变。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from governance.models import RiskLevel
from governance.policy import PolicyEngine
from tools.models import ToolSpec
from tools.registry import PermanentToolError, ToolRegistry, TransientToolError


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


async def _restart_service(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("service")
    if not service:
        raise PermanentToolError("service.restart requires a 'service' field")
    return {
        "action": "restart_service",
        "service": service,
        "executed": True,
        "mode": "reference",
        "restarted_at": _stamp(),
    }


async def _rollback_config(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("service")
    if not service:
        raise PermanentToolError("config.rollback requires a 'service' field")
    return {
        "action": "rollback_config",
        "service": service,
        "executed": True,
        "mode": "reference",
        "target_revision": payload.get("revision", "previous"),
        "rolled_back_at": _stamp(),
    }


async def _scale_out(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("service")
    if not service:
        raise PermanentToolError("service.scale_out requires a 'service' field")
    return {
        "action": "scale_out",
        "service": service,
        "executed": True,
        "mode": "reference",
        "replicas": int(payload.get("replicas", 4)),
    }


async def _escalate(payload: dict[str, Any], team: str) -> dict[str, Any]:
    return {
        "action": f"escalate_to_{team}",
        "service": payload.get("service", "unknown"),
        "executed": True,
        "mode": "reference",
        "ticket": f"{team.upper()}-{payload.get('workflow_id', 'na')[:8]}",
        "escalated_at": _stamp(),
    }


async def _escalate_to_dba(payload: dict[str, Any]) -> dict[str, Any]:
    return await _escalate(payload, "dba")


async def _escalate_to_network_team(payload: dict[str, Any]) -> dict[str, Any]:
    return await _escalate(payload, "network")


async def _create_ticket(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title")
    if not title:
        raise PermanentToolError("ticket.create requires a 'title' field")
    return {
        "title": title,
        "status": "open",
        "mode": "reference",
        "severity": payload.get("severity", "unknown"),
        "created_at": _stamp(),
    }


async def _lookup_topology(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("service", "unknown")
    known = {
        "checkout": ["payment", "inventory", "cart"],
        "payment": ["ledger", "bank-gateway"],
        "api": ["auth", "checkout"],
    }
    return {"service": service, "dependencies": known.get(service, []), "source": "reference-topology"}


async def _search_logs(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("service", "unknown")
    return {"service": service, "lines": list(payload.get("lines", [])), "source": "reference-logstore"}


async def _flaky(payload: dict[str, Any]) -> dict[str, Any]:
    """演示用：前 ``fail_times`` 次抛瞬时错误，之后成功。

    用于验证重试分级与熔断器行为（``GET /tools`` 可观察 ``retries`` 与 ``circuit_open``）。
    """
    state = _flaky.__dict__.setdefault("_state", {"attempts": 0})
    state["attempts"] += 1
    fail_times = int(payload.get("fail_times", 2))
    if state["attempts"] <= fail_times:
        raise TransientToolError(f"simulated transient failure #{state['attempts']}")
    return {"attempts": state["attempts"], "mode": "reference", "ok": True}


TOOL_SPECS: tuple[tuple[ToolSpec, Any], ...] = (
    (
        ToolSpec(
            name="topology.lookup",
            description="查询服务的上下游依赖，为根因推断提供拓扑上下文",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=3.0,
            max_retries=2,
            cacheable=True,
            cache_ttl_seconds=60.0,
            input_schema={"type": "object", "properties": {"service": {"type": "string"}}},
        ),
        _lookup_topology,
    ),
    (
        ToolSpec(
            name="logs.search",
            description="检索服务日志并返回结构化行",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=5.0,
            max_retries=2,
            cacheable=True,
            input_schema={"type": "object", "properties": {"service": {"type": "string"}}},
        ),
        _search_logs,
    ),
    (
        ToolSpec(
            name="service.restart",
            description="重启不健康的服务实例（高风险，需人工审批）",
            risk_level=RiskLevel.HIGH,
            required_permission="tool:high",
            timeout_seconds=5.0,
            max_retries=2,
            idempotent=True,
            resource_field="service",
            input_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
            output_schema={"type": "object", "properties": {"executed": {"type": "boolean"}}},
        ),
        _restart_service,
    ),
    (
        ToolSpec(
            name="config.rollback",
            description="回滚到上一个配置版本（高风险，需人工审批）",
            risk_level=RiskLevel.HIGH,
            required_permission="tool:high",
            timeout_seconds=8.0,
            max_retries=1,
            idempotent=True,
            resource_field="service",
            input_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
        ),
        _rollback_config,
    ),
    (
        ToolSpec(
            name="service.scale_out",
            description="扩容服务副本数以缓解资源耗尽（中风险）",
            risk_level=RiskLevel.MEDIUM,
            required_permission="tool:medium",
            timeout_seconds=10.0,
            max_retries=1,
            resource_field="service",
            input_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
        ),
        _scale_out,
    ),
    (
        ToolSpec(
            name="dba.escalate",
            description="升级到 DBA 团队处理数据库侧根因",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=5.0,
            max_retries=1,
            idempotent=True,
        ),
        _escalate_to_dba,
    ),
    (
        ToolSpec(
            name="network.escalate",
            description="升级到网络团队处理网络分区类根因",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=5.0,
            max_retries=1,
            idempotent=True,
        ),
        _escalate_to_network_team,
    ),
    (
        ToolSpec(
            name="ticket.create",
            description="创建工单，把无法自动修复的故障转人工",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=5.0,
            max_retries=1,
            idempotent=True,
            input_schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        ),
        _create_ticket,
    ),
    (
        ToolSpec(
            name="demo.flaky",
            description="演示用工具：前 N 次抛瞬时错误，用于验证重试分级与熔断",
            risk_level=RiskLevel.LOW,
            required_permission="tool:read",
            timeout_seconds=3.0,
            max_retries=3,
        ),
        _flaky,
    ),
)

ACTION_TO_TOOL: dict[str, str] = {
    "restart_service": "service.restart",
    "rollback_config": "config.rollback",
    "scale_out": "service.scale_out",
    "escalate_to_dba": "dba.escalate",
    "escalate_to_network_team": "network.escalate",
}
"""修复动作到工具的映射。``collect_more_evidence`` 刻意没有映射——它不产生副作用。"""

ACTION_RISK: dict[str, RiskLevel] = {
    action: next(spec.risk_level for spec, _ in TOOL_SPECS if spec.name == tool)
    for action, tool in ACTION_TO_TOOL.items()
}
"""动作的风险等级，来自工具规格的**单一事实源**（供没有注册表时降级使用）。"""


def build_default_tool_registry(
    policy: PolicyEngine | None = None,
    *,
    circuit_threshold: int = 3,
    cooldown_seconds: float = 30.0,
    **kwargs: Any,
) -> ToolRegistry:
    """构建包含全部 AIOps 工具的注册表。"""
    registry = ToolRegistry(
        policy=policy,
        circuit_threshold=circuit_threshold,
        cooldown_seconds=cooldown_seconds,
        **kwargs,
    )
    for spec, handler in TOOL_SPECS:
        registry.register(spec, handler)
    return registry


__all__ = ["ACTION_RISK", "ACTION_TO_TOOL", "TOOL_SPECS", "build_default_tool_registry"]
