from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any

from governance.models import Principal
from governance.policy import PolicyEngine
from observability.trace import AgentTrace
from tools.models import ToolHandler, ToolSpec


class ToolError(RuntimeError):
    pass


class ToolNotFound(ToolError):
    pass


class ToolPolicyDenied(ToolError):
    pass


class CircuitOpen(ToolError):
    pass


@dataclass
class _ToolEntry:
    spec: ToolSpec
    handler: ToolHandler
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0


class ToolRegistry:
    def __init__(self, policy: PolicyEngine | None = None, circuit_threshold: int = 3, cooldown_seconds: float = 30.0):
        self._tools: dict[str, _ToolEntry] = {}
        self.policy = policy or PolicyEngine()
        self.circuit_threshold = circuit_threshold
        self.cooldown_seconds = cooldown_seconds

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = _ToolEntry(spec=spec, handler=handler)

    def specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._tools.values()]

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any],
        principal: Principal,
        trace: AgentTrace,
        approved: bool = False,
    ) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolNotFound(name)
        if entry.circuit_open_until > monotonic():
            raise CircuitOpen(name)

        decision = self.policy.evaluate_tool(principal, entry.spec.risk_level, approved=approved)
        if not decision.allowed:
            raise ToolPolicyDenied(decision.reason)
        permissions = self.policy.permissions_for(principal)
        if entry.spec.required_permission not in permissions:
            raise ToolPolicyDenied(f"missing permission: {entry.spec.required_permission}")

        last_error: Exception | None = None
        for attempt in range(entry.spec.max_retries + 1):
            try:
                with trace.span("tool.invoke", tool=name, attempt=attempt, risk=int(entry.spec.risk_level)):
                    result = await asyncio.wait_for(entry.handler(payload), timeout=entry.spec.timeout_seconds)
                entry.consecutive_failures = 0
                return result
            except Exception as exc:
                last_error = exc
                entry.consecutive_failures += 1
                if entry.consecutive_failures >= self.circuit_threshold:
                    entry.circuit_open_until = monotonic() + self.cooldown_seconds
                if attempt < entry.spec.max_retries:
                    await asyncio.sleep(min(0.05 * (2**attempt), 0.5))
        raise ToolError(f"{name} failed after retries: {last_error}") from last_error
