import json
from pathlib import Path

import pytest

from agents.implementations import ReasoningAgent
from observability import AgentTrace
from runtime import AgentContext


@pytest.mark.asyncio
async def test_synthetic_reasoning_cases():
    cases = json.loads((Path(__file__).parent / "fixtures" / "synthetic_cases.json").read_text())
    agent = ReasoningAgent()
    for case in cases:
        evidence = [{"message": x} for x in case["logs"]]
        ctx = AgentContext(workflow_id=case["id"], trace_id="t", agent_name="reasoning", inputs={"evidence": evidence})
        out = await agent.run(ctx, AgentTrace("t"))
        assert out["root_cause"] == case["expected"]
