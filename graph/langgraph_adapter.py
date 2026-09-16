"""LangGraph 适配器（可选依赖）。

用 :mod:`graph.nodes` 里**同一批节点函数**构建等价拓扑，因此手写执行器与
LangGraph 执行器不会出现语义分叉——这是原实现的主要问题：adapter 里重新
写了一套 if/elif 推理逻辑，与 pipeline 各说各话。

拓扑（含真正的条件边，不再是清一色 ``add_edge``）::

    START -> normalize_alarm -> collect_topology -> collect_logs -> reason -> propose -> supervise
    supervise --"blocked"-------> blocked        -> END
    supervise --"needs_approval"-> needs_approval -> END
    supervise --"ready"---------> ready          -> END

三条出边分别对应「本体校验失败」「需人工审批」「可直接执行」，
由 :func:`graph.nodes.route_after_supervise` 在运行时依据 state 决定。

未安装 ``langgraph`` 时 :func:`langgraph_available` 返回 False，
核心链路仍可通过 :class:`graph.pipeline.IncidentReasoningGraph` 正常运行。
"""

from __future__ import annotations

from typing import Any

from graph.nodes import (
    GraphDeps,
    collect_logs,
    collect_topology,
    normalize_alarm,
    propose,
    reason,
    route_after_supervise,
    supervise,
    terminal,
)
from graph.state import IncidentGraphState


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


def build_state_graph(deps: GraphDeps):
    """用共享节点函数构建 LangGraph ``StateGraph``。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "install the orchestration extra: pip install -e '.[orchestration]'"
        ) from exc

    def bind(node):
        async def wrapper(state: IncidentGraphState) -> dict[str, Any]:
            return await node(dict(state), deps)

        wrapper.__name__ = getattr(node, "__name__", "node")
        return wrapper

    builder = StateGraph(IncidentGraphState)
    for name, node in (
        ("normalize_alarm", normalize_alarm),
        ("collect_topology", collect_topology),
        ("collect_logs", collect_logs),
        ("reason", reason),
        ("propose", propose),
        ("supervise", supervise),
    ):
        builder.add_node(name, bind(node))

    builder.add_node("blocked", bind(terminal))
    builder.add_node("needs_approval", bind(terminal))
    builder.add_node("ready", bind(terminal))

    builder.add_edge(START, "normalize_alarm")
    builder.add_edge("normalize_alarm", "collect_topology")
    builder.add_edge("collect_topology", "collect_logs")
    builder.add_edge("collect_logs", "reason")
    builder.add_edge("reason", "propose")
    builder.add_edge("propose", "supervise")
    builder.add_conditional_edges(
        "supervise",
        route_after_supervise,
        {"blocked": "blocked", "needs_approval": "needs_approval", "ready": "ready"},
    )
    for terminal_node in ("blocked", "needs_approval", "ready"):
        builder.add_edge(terminal_node, END)

    return builder.compile()


__all__ = ["build_state_graph", "langgraph_available"]
