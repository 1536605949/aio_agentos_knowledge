"""内层推理图执行器。

**定位**：这是短生命周期的推理图，不是持久化编排层。
它只在一次 Activity 内运行，做完「告警归一化 -> 拓扑/日志取证 -> 根因推理 ->
动作规划 -> 本体校验」就返回，不持有跨重启状态。

外层 :class:`temporal.workflow.IncidentWorkflowService` 才是持久化壳。

实现上刻意保持"手写执行器"而非强依赖 LangGraph：核心链路零外部依赖可运行，
``langgraph_adapter`` 用同一批节点构建等价拓扑，两者不会语义分叉。
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from agents.implementations import build_agents
from config import Settings, get_settings
from graph.nodes import NODE_FUNCTIONS, NODE_ORDER, GraphDeps, route_after_supervise, terminal
from observability.models import SpanKind
from observability.trace import AgentTrace
from ontology.domain import build_ontology
from ontology.models import OntologyRegistry
from tools.registry import ToolRegistry


class IncidentReasoningGraph:
    """故障推理图。"""

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        llm: Any = None,
        prompts: Any = None,
        ontology: OntologyRegistry | None = None,
        agents: dict[str, BaseAgent] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.tools = tools
        self.ontology = ontology or build_ontology()
        self.agents = agents or build_agents(
            llm=llm, prompts=prompts, ontology=self.ontology, tools=tools
        )

    # ------------------------------------------------------------------ 执行

    async def run(
        self,
        workflow_id: str,
        trace: AgentTrace,
        alarm: dict[str, Any],
        logs: list[str] | None = None,
        topology: dict[str, Any] | None = None,
        intent: str = "diagnose",
    ) -> dict[str, Any]:
        """执行完整推理链，返回合并后的状态字典。"""
        deps = GraphDeps(
            workflow_id=workflow_id,
            trace=trace,
            agents=self.agents,
            ontology=self.ontology,
            max_steps=self.settings.max_graph_steps,
        )
        state: dict[str, Any] = {
            "workflow_id": workflow_id,
            "intent": intent,
            "alarm": alarm,
            "logs": logs or [],
            "topology": topology or {},
            "approved": False,
            "errors": [],
        }

        with trace.span(
            "graph.run",
            kind=SpanKind.GRAPH_NODE,
            workflow_id=workflow_id,
            intent=intent,
            node_count=len(NODE_ORDER),
        ) as span:
            for name in NODE_ORDER:
                state.update(await NODE_FUNCTIONS[name](state, deps))
            state.update(await terminal(state, deps))
            span.attributes["route"] = route_after_supervise(state)

        state["steps"] = list(deps.steps)
        return state


__all__ = ["IncidentReasoningGraph"]
