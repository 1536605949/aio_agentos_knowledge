from __future__ import annotations

from pydantic import BaseModel, Field


class AgentCard(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    endpoint: str
    capabilities: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    auth_schemes: list[str] = Field(default_factory=lambda: ["bearer"])
    input_modes: list[str] = Field(default_factory=lambda: ["application/json"])
    output_modes: list[str] = Field(default_factory=lambda: ["application/json"])
