"""Intent -> Skill -> Capability -> Agent -> Tool -> Workflow 路由。

路由是"业务意图"进入系统的唯一入口。它把意图解析成一条**显式的执行契约**：
用哪个 Skill、需要什么 Capability、由哪个 Agent 执行、允许调用哪些 Tool、
走哪个 Workflow 模板。

这样做的价值不只是解耦：它让"Agent 能用哪些工具"成为**可审计的静态声明**，
而不是散落在代码里的隐式调用。
"""

from __future__ import annotations

from pydantic import BaseModel

from skills.catalog import DEFAULT_INTENT, INCIDENT_PIPELINE
from skills.models import SkillSpec
from skills.registry import SkillRegistry


class RouteDecision(BaseModel):
    """一次路由的完整结果。"""

    intent: str
    skill: str
    capability: str
    agent: str
    tools: list[str]
    workflow_template: str


class RouteSkillRouter:
    """意图路由器。"""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def route(self, intent: str) -> RouteDecision:
        """按意图路由；无匹配时抛 :class:`LookupError`。"""
        matches = self.registry.for_intent(intent)
        if not matches:
            raise LookupError(f"no skill registered for intent: {intent}")
        # 同名意图可能命中多个 Skill，取风险最低的一个作为默认路径
        skill: SkillSpec = sorted(matches, key=lambda spec: int(spec.risk_level))[0]
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

    def route_or_default(self, intent: str | None = None) -> RouteDecision:
        """路由失败时回落到默认意图，保证入口永远可用。"""
        try:
            return self.route(intent or DEFAULT_INTENT)
        except LookupError:
            return self.route(DEFAULT_INTENT)

    def plan(self, intent: str = DEFAULT_INTENT) -> list[RouteDecision]:
        """返回端到端处理一次故障的完整技能链。"""
        decisions: list[RouteDecision] = []
        for skill_name in INCIDENT_PIPELINE:
            spec = self.registry.skills.get(skill_name)
            if spec is None:
                continue
            decisions.append(
                RouteDecision(
                    intent=intent,
                    skill=spec.name,
                    capability=spec.required_capability,
                    agent=spec.allowed_agents[0] if spec.allowed_agents else "",
                    tools=list(spec.allowed_tools),
                    workflow_template=spec.workflow_template,
                )
            )
        return decisions

    def intents(self) -> list[str]:
        return sorted({intent for spec in self.registry.skills.values() for intent in spec.intents})

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "version": spec.version,
                "description": spec.description,
                "intents": list(spec.intents),
                "required_capability": spec.required_capability,
                "allowed_agents": list(spec.allowed_agents),
                "allowed_tools": list(spec.allowed_tools),
                "risk_level": int(spec.risk_level),
                "workflow_template": spec.workflow_template,
            }
            for spec in sorted(self.registry.skills.values(), key=lambda spec: spec.name)
        ]


__all__ = ["RouteDecision", "RouteSkillRouter"]
