"""Agent 注册表。

把"系统里有哪些 Agent、各自声明了什么能力/技能/工具权限"变成可查询的运行时事实，
供 ``GET /agents`` 端点与 Supervisor 校验消费。
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from agents.implementations import build_agents
from agents.specs import AgentSpec


class AgentRegistry:
    """按名字索引 Agent 实例，并暴露其声明式规格。"""

    def __init__(self, agents: dict[str, BaseAgent] | None = None) -> None:
        self._agents: dict[str, BaseAgent] = agents if agents is not None else build_agents()

    @property
    def agents(self) -> dict[str, BaseAgent]:
        return dict(self._agents)

    def get(self, name: str) -> BaseAgent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"agent not registered: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._agents)

    def specs(self) -> list[AgentSpec]:
        return [self._agents[name].spec for name in self.names()]

    def capabilities(self) -> dict[str, str]:
        """能力 -> Agent 名。用于校验 Skill 声明的 capability 是否有承接者。"""
        mapping: dict[str, str] = {}
        for name in self.names():
            for capability in self._agents[name].spec.capabilities:
                mapping[capability] = name
        return mapping

    def tools_for(self, name: str) -> list[str]:
        return list(self.get(name).spec.tool_permissions)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "capabilities": list(spec.capabilities),
                "skills": list(spec.skills),
                "tool_permissions": list(spec.tool_permissions),
                "memory_policy": {
                    "read_types": list(spec.memory_policy.read_types),
                    "write_types": list(spec.memory_policy.write_types),
                },
                "evaluation_metrics": [
                    {"name": metric.name, "target": metric.target} for metric in spec.evaluation_metrics
                ],
            }
            for spec in sorted(self.specs(), key=lambda spec: spec.name)
        ]


__all__ = ["AgentRegistry"]
