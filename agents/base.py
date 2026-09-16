from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agents.specs import AgentSpec
from observability.trace import AgentTrace
from runtime.context import AgentContext
from runtime.lifecycle import AgentLifecycle, transition


class BaseAgent(ABC):
    spec: AgentSpec

    async def run(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        context.lifecycle = transition(context.lifecycle, AgentLifecycle.RUNNING)
        try:
            with trace.span("agent.run", agent=self.spec.name):
                output = await self.execute(context, trace)
            context.outputs = output
            context.lifecycle = transition(context.lifecycle, AgentLifecycle.COMPLETED)
            return output
        except Exception:
            context.lifecycle = transition(context.lifecycle, AgentLifecycle.FAILED)
            raise

    @abstractmethod
    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        raise NotImplementedError
