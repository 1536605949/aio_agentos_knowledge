from __future__ import annotations

from typing import Any

from graph.nodes import supervisor_node
from graph.state import IncidentGraphState


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


def build_state_graph():
    """Build the production LangGraph topology.

    Nodes are intentionally pure/deterministic here; production agent/tool adapters can
    replace node bodies while preserving the topology and supervisor contract.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("install the orchestration extra: pip install -e '.[orchestration]'") from exc

    async def collect_logs(state: IncidentGraphState) -> dict[str, Any]:
        evidence = [
            {"message": line, "signal": "error" if "error" in line.lower() else "info"}
            for line in state.get("logs", [])
        ]
        return {"evidence": evidence}

    async def reason(state: IncidentGraphState) -> dict[str, Any]:
        messages = " ".join(x.get("message", "") for x in state.get("evidence", [])).lower()
        if "database" in messages or "db" in messages:
            cause, confidence = "database_dependency_failure", 0.8
        elif "timeout" in messages:
            cause, confidence = "upstream_timeout", 0.8
        elif "memory" in messages or "oom" in messages:
            cause, confidence = "resource_exhaustion", 0.8
        else:
            cause, confidence = "undetermined", 0.2
        return {"root_cause": cause, "confidence": confidence}

    async def propose(state: IncidentGraphState) -> dict[str, Any]:
        cause = state.get("root_cause", "undetermined")
        action = "restart_service" if cause in {"upstream_timeout", "resource_exhaustion"} else "collect_more_evidence"
        return {"proposed_action": action}

    async def supervise(state: IncidentGraphState) -> dict[str, Any]:
        return supervisor_node(dict(state))

    builder = StateGraph(IncidentGraphState)
    builder.add_node("collect_logs", collect_logs)
    builder.add_node("reason", reason)
    builder.add_node("propose", propose)
    builder.add_node("supervisor", supervise)
    builder.add_edge(START, "collect_logs")
    builder.add_edge("collect_logs", "reason")
    builder.add_edge("reason", "propose")
    builder.add_edge("propose", "supervisor")
    builder.add_edge("supervisor", END)
    return builder.compile()
