from __future__ import annotations

from pydantic import BaseModel

from skills.models import SkillSpec
from skills.registry import SkillRegistry


class RouteDecision(BaseModel):
    intent: str
    skill: str
    capability: str
    agent: str
    tools: list[str]
    workflow_template: str


class RouteSkillRouter:
    """Intent -> Skill -> Capability -> Agent -> Tool -> Workflow."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def route(self, intent: str) -> RouteDecision:
        matches = self.registry.for_intent(intent)
        if not matches:
            raise LookupError(f"no skill registered for intent: {intent}")
        skill: SkillSpec = sorted(matches, key=lambda s: int(s.risk_level))[0]
        if not skill.allowed_agents:
            raise LookupError(f"skill has no allowed agent: {skill.name}")
        return RouteDecision(
            intent=intent,
            skill=skill.name,
            capability=skill.required_capability,
            agent=skill.allowed_agents[0],
            tools=skill.allowed_tools,
            workflow_template=skill.workflow_template,
        )
