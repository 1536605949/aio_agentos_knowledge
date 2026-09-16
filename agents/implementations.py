"""6 个 AIOps Agent 的参考实现。

与早期版本的关键差异：

- **不再有硬编码 if/elif 根因推断**。推理与动作规划都通过
  :meth:`agents.base.BaseAgent.call_llm` 完成，提示词里注入本体词表白名单。
- **输出必须落在本体词表内**。越界值会被收敛到安全出口
  （根因 -> ``undetermined``，动作 -> ``collect_more_evidence``），
  并在返回值里标记 ``ontology_violation`` 供 Supervisor 与 BadCase 归档使用。
- **审批门由工具声明的风险等级驱动**，不再依赖 ``action == "restart_service"`` 这类启发式。
- **所有 6 个 Agent 都在运行时可达**（含 Topology / Ticket），不再有"写好了但没人调"的组件。
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import BaseAgent
from agents.specs import AgentSpec, EvaluationMetric, MemoryPolicy
from observability.trace import AgentTrace
from ontology.domain import (
    ALARM_CATEGORIES,
    REMEDIATION_ACTIONS,
    ROOT_CAUSES,
    SEVERITIES,
    actions_for_prompt,
    is_valid_action,
    is_valid_root_cause,
    root_causes_for_prompt,
    severities_for_prompt,
)
from runtime.context import AgentContext
from tools.catalog import ACTION_RISK, ACTION_TO_TOOL
from tools.registry import ToolRegistry

_HIGH_RISK = 3
"""``RiskLevel.HIGH`` 的数值，避免在模块级引入 governance 依赖。"""


def _spec(
    name: str,
    capability: str,
    skill: str,
    tools: list[str],
    output_properties: dict[str, Any],
) -> AgentSpec:
    return AgentSpec(
        name=name,
        capabilities=[capability],
        skills=[skill],
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": output_properties},
        tool_permissions=tools,
        memory_policy=MemoryPolicy(read_types=["short", "episodic"], write_types=["short"]),
        evaluation_metrics=[
            EvaluationMetric(name="schema_validity", target=1.0),
            EvaluationMetric(name="ontology_conformance", target=1.0),
        ],
    )


def _render_evidence(evidence: list[Any]) -> str:
    lines: list[str] = []
    for index, item in enumerate(evidence, start=1):
        if isinstance(item, dict):
            message = item.get("message", "")
            signal = item.get("signal", "info")
        else:
            message, signal = str(item), "info"
        lines.append(f"{index}. [{signal}] {message}")
    return "\n".join(lines) if lines else "(无证据)"


def _summarize(evidence: list[Any], limit: int = 3) -> str:
    messages = [str(item.get("message", "")) if isinstance(item, dict) else str(item) for item in evidence]
    if not messages:
        return "(无)"
    return " | ".join(messages[:limit])


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return round(min(high, max(low, number)), 4)


class AlarmAgent(BaseAgent):
    """告警标准化：统一 severity 与分类，供下游推理消费。"""

    spec = _spec(
        "alarm",
        "alarm_normalization",
        "normalize_alarm",
        [],
        {"normalized_alarm": {"type": "object"}},
    )

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        alarm = dict(context.inputs.get("alarm") or context.inputs)
        payload, response = await self.call_llm(
            task="alarm",
            system_prompt="alarm.system",
            user_prompt="alarm.user",
            variables={
                "allowed_severities": severities_for_prompt(),
                "alarm_json": json.dumps(alarm, ensure_ascii=False, default=str),
            },
            trace=trace,
            metadata={"allowed_severities": list(SEVERITIES)},
        )

        severity = str(payload.get("severity", "unknown")).lower()
        if severity not in SEVERITIES:
            severity = "unknown"
        category = str(payload.get("category", "unclassified"))
        if category not in ALARM_CATEGORIES:
            category = "unclassified"

        normalized = {
            **alarm,
            "severity": severity,
            "category": category,
            "summary": str(payload.get("summary") or alarm.get("message", "")),
            "normalized_by": response.provider,
        }
        return {"normalized_alarm": normalized, "severity": severity}


class TopologyAgent(BaseAgent):
    """拓扑取证：查询服务依赖，为根因推断提供上下文。"""

    spec = _spec(
        "topology",
        "topology_evidence",
        "collect_topology",
        ["topology.lookup"],
        {"topology": {"type": "object"}},
    )

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        topology = context.inputs.get("topology")
        if not topology and self.tools is not None and self.tools.has("topology.lookup"):
            alarm = context.inputs.get("alarm") or {}
            topology = await self.call_tool(
                "topology.lookup",
                {"service": str(alarm.get("service", "unknown"))},
                trace,
            )
        return {"topology": topology or {"dependencies": []}}


class LogAgent(BaseAgent):
    """日志取证：把原始日志行结构化为带信号标记的证据。"""

    spec = _spec("log", "log_evidence", "collect_logs", ["logs.search"], {"evidence": {"type": "array"}})

    _ERROR_HINTS = ("error", "timeout", "oom", "refused", "fail", "exception", "panic", "unreachable")

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        logs = context.inputs.get("logs") or []
        if not logs and self.tools is not None and self.tools.has("logs.search"):
            alarm = context.inputs.get("alarm") or {}
            result = await self.call_tool(
                "logs.search",
                {"service": str(alarm.get("service", "unknown")), "lines": []},
                trace,
            )
            logs = result.get("lines", [])

        evidence = [
            {
                "message": str(line),
                "signal": "error" if any(hint in str(line).lower() for hint in self._ERROR_HINTS) else "info",
            }
            for line in logs
        ]
        return {"evidence": evidence}


class ReasoningAgent(BaseAgent):
    """根因推理：在受本体词表约束的取值空间内给出根因与置信度。"""

    spec = _spec(
        "reasoning",
        "root_cause_reasoning",
        "infer_root_cause",
        [],
        {"root_cause": {"type": "string"}, "confidence": {"type": "number"}},
    )

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        alarm = context.inputs.get("alarm") or {}
        evidence = context.inputs.get("evidence") or []
        topology = context.inputs.get("topology") or {}

        payload, response = await self.call_llm(
            task="reasoning",
            system_prompt="reasoning.system",
            user_prompt="reasoning.user",
            variables={
                "allowed_causes": root_causes_for_prompt(),
                "service": str(alarm.get("service", "unknown")),
                "severity": str(alarm.get("severity", "unknown")),
                "message": str(alarm.get("message", "")),
                "evidence": _render_evidence(evidence),
                "topology": json.dumps(topology, ensure_ascii=False, default=str),
            },
            trace=trace,
            metadata={"allowed_causes": list(ROOT_CAUSES)},
        )

        raw_cause = str(payload.get("root_cause", "undetermined"))
        cause = raw_cause if is_valid_root_cause(raw_cause) else "undetermined"
        confidence = _clamp(payload.get("confidence", 0.0))
        if cause == "undetermined":
            confidence = min(confidence, 0.3)

        return {
            "root_cause": cause,
            "confidence": confidence,
            "rationale": str(payload.get("rationale", "")),
            "evidence_refs": [str(item) for item in (payload.get("evidence_refs") or [])][:5],
            "raw_root_cause": raw_cause,
            "ontology_violation": raw_cause != cause,
            "llm_provider": response.provider,
            "llm_fallback_used": response.fallback_used,
        }


class RemediationAgent(BaseAgent):
    """动作规划：从本体词表选动作，并由工具风险等级推导是否需要人工审批。"""

    spec = _spec(
        "remediation",
        "remediation",
        "remediate_incident",
        ["service.restart"],
        {"action": {"type": "string"}},
    )

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        alarm = context.inputs.get("alarm") or {}
        root_cause = str(context.inputs.get("root_cause", "undetermined"))

        payload, response = await self.call_llm(
            task="remediation",
            system_prompt="remediation.system",
            user_prompt="remediation.user",
            variables={
                "allowed_actions": actions_for_prompt(),
                "root_cause": root_cause,
                "confidence": context.inputs.get("confidence", 0.0),
                "service": str(context.inputs.get("service") or alarm.get("service", "unknown")),
                "evidence_summary": _summarize(context.inputs.get("evidence") or []),
            },
            trace=trace,
            metadata={"allowed_actions": list(REMEDIATION_ACTIONS)},
        )

        raw_action = str(payload.get("action", "collect_more_evidence"))
        action = raw_action if is_valid_action(raw_action) else "collect_more_evidence"
        tool = ACTION_TO_TOOL.get(action)

        return {
            "action": action,
            "tool": tool,
            "requires_approval": self._requires_approval(tool, action),
            "rationale": str(payload.get("rationale", "")),
            "risk_notes": str(payload.get("risk_notes", "")),
            "raw_action": raw_action,
            "ontology_violation": raw_action != action,
            "llm_provider": response.provider,
        }

    def _requires_approval(self, tool: str | None, action: str) -> bool:
        """审批门来自工具规格声明的风险等级——单一事实源。"""
        if tool is None:
            return False
        if self.tools is not None and self.tools.has(tool):
            return int(self.tools.get(tool).risk_level) >= _HIGH_RISK
        return int(ACTION_RISK.get(action, 0)) >= _HIGH_RISK


class TicketAgent(BaseAgent):
    """工单升级：把无法自动修复的故障转人工。"""

    spec = _spec("ticket", "ticketing", "create_ticket", ["ticket.create"], {"ticket": {"type": "object"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        inputs = context.inputs
        payload = {
            "title": str(inputs.get("title") or f"AIOps incident: {inputs.get('root_cause', 'unknown')}"),
            "service": str(inputs.get("service", "unknown")),
            "severity": str(inputs.get("severity", "unknown")),
            "root_cause": str(inputs.get("root_cause", "undetermined")),
            "workflow_id": context.workflow_id,
        }
        if self.tools is not None and self.tools.has("ticket.create"):
            return {"ticket": await self.call_tool("ticket.create", payload, trace)}
        return {"ticket": {**payload, "status": "prepared"}}


def build_agents(
    llm: Any = None,
    prompts: Any = None,
    ontology: Any = None,
    tools: ToolRegistry | None = None,
) -> dict[str, BaseAgent]:
    """构建全部 Agent 实例，共享同一套 LLM / 提示词 / 本体 / 工具依赖。"""
    kwargs = {"llm": llm, "prompts": prompts, "ontology": ontology, "tools": tools}
    return {
        "alarm": AlarmAgent(**kwargs),
        "topology": TopologyAgent(**kwargs),
        "log": LogAgent(**kwargs),
        "reasoning": ReasoningAgent(**kwargs),
        "remediation": RemediationAgent(**kwargs),
        "ticket": TicketAgent(**kwargs),
    }


AGENTS: dict[str, BaseAgent] = build_agents()
"""模块级默认实例（确定性 LLM、无工具注册表），供向后兼容与简单脚本使用。"""


__all__ = [
    "AGENTS",
    "AlarmAgent",
    "LogAgent",
    "ReasoningAgent",
    "RemediationAgent",
    "TicketAgent",
    "TopologyAgent",
    "build_agents",
]
