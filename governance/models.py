"""治理层的数据契约：风险等级、主体与策略判定。"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field


class RiskLevel(IntEnum):
    """工具风险等级。数值化便于比较（``spec.risk_level >= RiskLevel.HIGH``）。"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FORBIDDEN = 4


class Role(StrEnum):
    """内置角色。权限映射见 :data:`governance.policy.ROLE_PERMISSIONS`。"""

    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class Principal(BaseModel):
    """调用主体。可以是人（on-call 工程师）或系统身份（workflow-executor）。"""

    subject: str
    roles: set[str] = Field(default_factory=set)


class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


__all__ = ["PolicyDecision", "Principal", "RiskLevel", "Role"]
