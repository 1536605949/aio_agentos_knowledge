from __future__ import annotations

from pydantic import BaseModel, Field

from governance.models import RiskLevel


class CapabilitySpec(BaseModel):
    name: str
    description: str = ""


class SkillSpec(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    intents: list[str] = Field(default_factory=list)
    required_capability: str
    allowed_agents: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    workflow_template: str = "incident_response"
