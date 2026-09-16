from __future__ import annotations

from agents.implementations import LogAgent, ReasoningAgent, RemediationAgent
from observability.trace import AgentTrace
from runtime.context import AgentContext


class IncidentReasoningGraph:
    """Short-lived inner reasoning graph. Durable state belongs to the outer workflow."""

    def __init__(self) -> None:
        self.log_agent = LogAgent()
        self.reasoning_agent = ReasoningAgent()
        self.remediation_agent = RemediationAgent()

    async def run(self, workflow_id: str, trace: AgentTrace, alarm: dict, logs: list[str]) -> dict:
        log_ctx = AgentContext(workflow_id=workflow_id, trace_id=trace.trace_id, agent_name="log", inputs={"alarm": alarm, "logs": logs})
        log_out = await self.log_agent.run(log_ctx, trace)

        reasoning_ctx = AgentContext(
            workflow_id=workflow_id, trace_id=trace.trace_id, agent_name="reasoning",
            inputs={"alarm": alarm, "evidence": log_out["evidence"]},
        )
        reasoning_out = await self.reasoning_agent.run(reasoning_ctx, trace)

        remediation_ctx = AgentContext(
            workflow_id=workflow_id, trace_id=trace.trace_id, agent_name="remediation",
            inputs=reasoning_out,
        )
        remediation_out = await self.remediation_agent.run(remediation_ctx, trace)
        return {**log_out, **reasoning_out, "proposed_action": remediation_out["action"], "requires_approval": remediation_out["requires_approval"]}
