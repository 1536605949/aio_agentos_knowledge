import pytest

from governance.models import Principal, RiskLevel
from observability import AgentTrace
from tools import ToolPolicyDenied, ToolRegistry, ToolSpec


@pytest.mark.asyncio
async def test_high_risk_requires_approval():
    calls = []

    async def handler(payload):
        calls.append(payload)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(ToolSpec(name="service.restart", risk_level=RiskLevel.HIGH), handler)
    principal = Principal(subject="alice", roles={"admin"})
    trace = AgentTrace()

    with pytest.raises(ToolPolicyDenied):
        await registry.invoke("service.restart", {}, principal, trace, approved=False)
    assert calls == []

    result = await registry.invoke("service.restart", {}, principal, trace, approved=True)
    assert result == {"ok": True}
    assert len(trace.spans()) == 1
