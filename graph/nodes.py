"""内层推理图的节点实现。

每个节点都是 ``async (state, deps) -> partial_state`` 的**纯增量函数**：
只返回自己新增/覆盖的字段，不修改传入的 state。
这样同一批节点既能被手写执行器（:mod:`graph.pipeline`）顺序驱动，
也能原样挂到 LangGraph 的 ``StateGraph`` 上（:mod:`graph.langgraph_adapter`），
不会出现"两套实现语义分叉"的问题。

依赖通过 :class:`GraphDeps` 显式注入，节点本身不构造任何对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.base import BaseAgent
from observability.models import SpanKind
from observability.trace import AgentTrace
from ontology.models import OntologyRegistry
from runtime.context import AgentContext

NODE_ORDER: tuple[str, ...] = (
    "normalize_alarm",
    "collect_topology",
    "collect_logs",
    "reason",
    "propose",
    "supervise",
)
"""节点执行顺序。也是 Supervisor 校验"图是否完整跑完"的依据。"""


@dataclass
class GraphDeps:
    """节点运行所需的全部外部依赖。"""

    workflow_id: str
    trace: AgentTrace
    agents: dict[str, BaseAgent]
    ontology: OntologyRegistry
    max_steps: int = 24
    steps: list[str] = field(default_factory=list)

    def context(self, agent_name: str, inputs: dict[str, Any]) -> AgentContext:
        return AgentContext(
            workflow_id=self.workflow_id,
            trace_id=self.trace.trace_id,
            agent_name=agent_name,
            inputs=inputs,
        )


def _alarm_of(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("normalized_alarm") or state.get("alarm") or {}


async def normalize_alarm(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """把原始告警归一化，并把 ``severity`` 提升到顶层供本体公理校验。"""
    deps.steps.append("normalize_alarm")
    output = await deps.agents["alarm"].run(
        deps.context("alarm", {"alarm": state.get("alarm") or {}}), deps.trace
    )
    return {
        "normalized_alarm": output["normalized_alarm"],
        "severity": output["severity"],
        "steps": list(deps.steps),
    }


async def collect_topology(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """采集服务依赖拓扑。"""
    deps.steps.append("collect_topology")
    output = await deps.agents["topology"].run(
        deps.context("topology", {"alarm": _alarm_of(state), "topology": state.get("topology")}),
        deps.trace,
    )
    return {"topology": output["topology"], "steps": list(deps.steps)}


async def collect_logs(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """把日志行结构化为证据列表。"""
    deps.steps.append("collect_logs")
    output = await deps.agents["log"].run(
        deps.context("log", {"alarm": _alarm_of(state), "logs": state.get("logs", [])}),
        deps.trace,
    )
    return {"evidence": output["evidence"], "steps": list(deps.steps)}


async def reason(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """根因推理（LLM 调用点 1）。"""
    deps.steps.append("reason")
    output = await deps.agents["reasoning"].run(
        deps.context(
            "reasoning",
            {
                "alarm": _alarm_of(state),
                "evidence": state.get("evidence", []),
                "topology": state.get("topology", {}),
            },
        ),
        deps.trace,
    )
    return {
        "root_cause": output["root_cause"],
        "confidence": output["confidence"],
        "rationale": output.get("rationale", ""),
        "evidence_refs": output.get("evidence_refs", []),
        "ontology_violation": bool(output.get("ontology_violation")),
        "steps": list(deps.steps),
    }


async def propose(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """修复动作规划（LLM 调用点 2），并推导审批需求。"""
    deps.steps.append("propose")
    output = await deps.agents["remediation"].run(
        deps.context(
            "remediation",
            {
                "root_cause": state.get("root_cause", "undetermined"),
                "confidence": state.get("confidence", 0.0),
                "service": _alarm_of(state).get("service", "unknown"),
                "alarm": _alarm_of(state),
                "evidence": state.get("evidence", []),
            },
        ),
        deps.trace,
    )
    return {
        "proposed_action": output["action"],
        "action_tool": output.get("tool"),
        "action_rationale": output.get("rationale", ""),
        "requires_approval": bool(output["requires_approval"]),
        "ontology_violation": bool(state.get("ontology_violation")) or bool(output.get("ontology_violation")),
        "steps": list(deps.steps),
    }


async def supervise(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """Supervisor 节点：本体公理校验 + 最小输出形状校验。

    这是"LLM 只能在业务边界内生成内容"的可执行落点：
    越界的 ``root_cause`` / ``severity`` 会在这里被记录为错误。
    """
    with deps.trace.span("graph.supervise", kind=SpanKind.GOVERNANCE, workflow_id=deps.workflow_id) as span:
        deps.steps.append("supervise")
        errors = list(state.get("errors", []))
        errors.extend(deps.ontology.validate(dict(state)))
        if not state.get("workflow_id"):
            errors.append("workflow_id missing")
        if state.get("root_cause") is None:
            errors.append("root_cause missing")
        if state.get("proposed_action") is None:
            errors.append("proposed_action missing")
        missing_nodes = [name for name in NODE_ORDER[:-1] if name not in deps.steps]
        if missing_nodes:
            errors.append(f"graph did not execute nodes: {missing_nodes}")
        if len(deps.steps) > deps.max_steps:
            errors.append(f"graph exceeded max steps ({deps.max_steps})")
        span.attributes["error_count"] = len(errors)
        span.attributes["nodes"] = len(deps.steps)

    return {"errors": errors, "steps": list(deps.steps)}


def route_after_supervise(state: dict[str, Any]) -> str:
    """条件边：本体校验失败 -> blocked；需审批 -> needs_approval；否则 ready。"""
    if state.get("errors"):
        return "blocked"
    if state.get("requires_approval"):
        return "needs_approval"
    return "ready"


async def terminal(state: dict[str, Any], deps: GraphDeps) -> dict[str, Any]:
    """终结节点：把路由结果写回 state，供外层工作流消费。"""
    return {"steps": list(deps.steps)}


NODE_FUNCTIONS = {
    "normalize_alarm": normalize_alarm,
    "collect_topology": collect_topology,
    "collect_logs": collect_logs,
    "reason": reason,
    "propose": propose,
    "supervise": supervise,
}


__all__ = [
    "NODE_FUNCTIONS",
    "NODE_ORDER",
    "GraphDeps",
    "collect_logs",
    "collect_topology",
    "normalize_alarm",
    "propose",
    "reason",
    "route_after_supervise",
    "supervise",
    "terminal",
]
