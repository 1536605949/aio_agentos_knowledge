from governance.models import RiskLevel
from router import RouteSkillRouter
from skills import CapabilitySpec, SkillRegistry, SkillSpec


def test_route_skill_chain():
    registry = SkillRegistry()
    registry.register_capability(CapabilitySpec(name="root_cause_reasoning"))
    registry.register_skill(SkillSpec(
        name="infer_root_cause",
        intents=["diagnose"],
        required_capability="root_cause_reasoning",
        allowed_agents=["reasoning"],
        allowed_tools=[],
        risk_level=RiskLevel.LOW,
    ))
    decision = RouteSkillRouter(registry).route("diagnose")
    assert decision.skill == "infer_root_cause"
    assert decision.agent == "reasoning"
    assert decision.capability == "root_cause_reasoning"
