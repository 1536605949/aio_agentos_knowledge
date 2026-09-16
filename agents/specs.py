from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryPolicy(BaseModel):
    read_types: list[str] = Field(default_factory=list)
    write_types: list[str] = Field(default_factory=list)


class EvaluationMetric(BaseModel):
    name: str
    target: float = Field(ge=0.0, le=1.0)


class AgentSpec(BaseModel):
    name: str
    capabilities: list[str]
    skills: list[str]
    input_schema: dict
    output_schema: dict
    tool_permissions: list[str] = Field(default_factory=list)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    evaluation_metrics: list[EvaluationMetric] = Field(default_factory=list)
