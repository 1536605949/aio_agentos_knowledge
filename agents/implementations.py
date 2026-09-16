from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from agents.specs import AgentSpec, EvaluationMetric, MemoryPolicy
from observability.trace import AgentTrace
from runtime.context import AgentContext


def _spec(name: str, capability: str, skill: str, tools: list[str], output_properties: dict[str, Any]) -> AgentSpec:
    return AgentSpec(
        name=name,
        capabilities=[capability],
        skills=[skill],
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": output_properties},
        tool_permissions=tools,
        memory_policy=MemoryPolicy(read_types=["short", "episodic"], write_types=["short"]),
        evaluation_metrics=[EvaluationMetric(name="schema_validity", target=1.0)],
    )


class AlarmAgent(BaseAgent):
    spec = _spec("alarm", "alarm_normalization", "normalize_alarm", [], {"normalized_alarm": {"type": "object"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        alarm = dict(context.inputs.get("alarm", context.inputs))
        severity = str(alarm.get("severity", "unknown")).lower()
        return {"normalized_alarm": {**alarm, "severity": severity}}


class TopologyAgent(BaseAgent):
    spec = _spec("topology", "topology_evidence", "collect_topology", ["topology.lookup"], {"topology": {"type": "object"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        return {"topology": context.inputs.get("topology", {"dependencies": []})}


class LogAgent(BaseAgent):
    spec = _spec("log", "log_evidence", "collect_logs", ["logs.search"], {"evidence": {"type": "array"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        logs = context.inputs.get("logs", [])
        evidence = [{"message": str(x), "signal": "error" if "error" in str(x).lower() else "info"} for x in logs]
        return {"evidence": evidence}


class ReasoningAgent(BaseAgent):
    spec = _spec("reasoning", "root_cause_reasoning", "infer_root_cause", [], {"root_cause": {"type": "string"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        evidence = context.inputs.get("evidence", [])
        messages = " ".join(str(x.get("message", "")) for x in evidence if isinstance(x, dict)).lower()
        if "database" in messages or "db" in messages:
            cause = "database_dependency_failure"
        elif "timeout" in messages:
            cause = "upstream_timeout"
        elif "memory" in messages or "oom" in messages:
            cause = "resource_exhaustion"
        else:
            cause = "undetermined"
        return {"root_cause": cause, "confidence": 0.8 if cause != "undetermined" else 0.2}


class RemediationAgent(BaseAgent):
    spec = _spec("remediation", "remediation", "remediate_incident", ["service.restart"], {"action": {"type": "string"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        root_cause = context.inputs.get("root_cause", "undetermined")
        action = "restart_service" if root_cause in {"upstream_timeout", "resource_exhaustion"} else "collect_more_evidence"
        return {"action": action, "requires_approval": action == "restart_service"}


class TicketAgent(BaseAgent):
    spec = _spec("ticket", "ticketing", "create_ticket", ["ticket.create"], {"ticket": {"type": "object"}})

    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        return {"ticket": {"title": context.inputs.get("title", "AIOps incident"), "status": "prepared"}}


AGENTS = {
    "alarm": AlarmAgent(),
    "topology": TopologyAgent(),
    "log": LogAgent(),
    "reasoning": ReasoningAgent(),
    "remediation": RemediationAgent(),
    "ticket": TicketAgent(),
}
