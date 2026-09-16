from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from runtime.lifecycle import AgentLifecycle


class AgentContext(BaseModel):
    """Ephemeral per-agent-call state. It is never the durable workflow source of truth."""

    workflow_id: str
    trace_id: str
    agent_name: str
    lifecycle: AgentLifecycle = AgentLifecycle.CREATED
    inputs: dict[str, Any] = Field(default_factory=dict)
    memory_refs: list[str] = Field(default_factory=list)
    permissions: set[str] = Field(default_factory=set)
    outputs: dict[str, Any] = Field(default_factory=dict)
