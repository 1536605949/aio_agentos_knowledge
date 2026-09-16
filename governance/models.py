from __future__ import annotations

from enum import IntEnum
from pydantic import BaseModel, Field


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FORBIDDEN = 4


class Role(str):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class Principal(BaseModel):
    subject: str
    roles: set[str] = Field(default_factory=set)


class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
