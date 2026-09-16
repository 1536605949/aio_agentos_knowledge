from __future__ import annotations

from governance.models import PolicyDecision, Principal, RiskLevel


class PolicyEngine:
    """Tool-call interceptor. Every tool call passes through this engine."""

    ROLE_PERMISSIONS = {
        "viewer": {"tool:read"},
        "operator": {"tool:read", "tool:medium"},
        "approver": {"tool:read", "tool:medium", "approval:decide"},
        "admin": {"tool:read", "tool:medium", "approval:decide", "tool:high"},
    }

    def permissions_for(self, principal: Principal) -> set[str]:
        permissions: set[str] = set()
        for role in principal.roles:
            permissions |= self.ROLE_PERMISSIONS.get(role, set())
        return permissions

    def evaluate_tool(self, principal: Principal, risk_level: RiskLevel, approved: bool = False) -> PolicyDecision:
        perms = self.permissions_for(principal)
        if risk_level == RiskLevel.FORBIDDEN:
            return PolicyDecision(allowed=False, reason="forbidden risk level")
        if risk_level == RiskLevel.HIGH:
            if not approved:
                return PolicyDecision(allowed=False, requires_approval=True, reason="human approval required")
            if "tool:high" not in perms and "approval:decide" not in perms:
                return PolicyDecision(allowed=False, reason="principal lacks high-risk permission")
            return PolicyDecision(allowed=True, reason="approved high-risk action")
        if risk_level == RiskLevel.MEDIUM and "tool:medium" not in perms:
            return PolicyDecision(allowed=False, reason="principal lacks medium-risk permission")
        if risk_level == RiskLevel.LOW and "tool:read" not in perms:
            return PolicyDecision(allowed=False, reason="principal lacks tool permission")
        return PolicyDecision(allowed=True, reason="policy passed")
