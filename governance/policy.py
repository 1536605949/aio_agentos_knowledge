"""策略引擎：工具调用的治理拦截器。

设计要点：

- **角色到权限是显式表**，不是散落在业务代码里的 if。
- **高风险动作必须显式审批**：``approved=False`` 时返回 ``requires_approval``，
  而不是放行。
- **职责分离**：批准高风险动作的 ``approver`` 角色**不具备** ``tool:high`` 权限，
  真正执行的是独立的 ``workflow-executor`` 身份。
  原实现写成 ``if "tool:high" not in perms and "approval:decide" not in perms``，
  等于让审批者同时获得执行权，破坏了职责分离——这里已修正为只认 ``tool:high``。
"""

from __future__ import annotations

from governance.models import PolicyDecision, Principal, RiskLevel

#: 角色 -> 权限集合。新增角色只需在此声明。
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"tool:read"}),
    "operator": frozenset({"tool:read", "tool:medium"}),
    "approver": frozenset({"tool:read", "tool:medium", "approval:decide"}),
    "admin": frozenset({"tool:read", "tool:medium", "tool:high", "approval:decide"}),
}


class PolicyEngine:
    """工具调用与审批的准入判定。"""

    ROLE_PERMISSIONS = ROLE_PERMISSIONS

    @property
    def known_roles(self) -> frozenset[str]:
        return frozenset(ROLE_PERMISSIONS)

    def permissions_for(self, principal: Principal) -> set[str]:
        """把 principal 的角色集合展开为权限集合；未知角色不贡献任何权限。"""
        permissions: set[str] = set()
        for role in principal.roles:
            permissions |= ROLE_PERMISSIONS.get(role, frozenset())
        return permissions

    def evaluate_tool(
        self,
        principal: Principal,
        risk_level: RiskLevel,
        approved: bool = False,
    ) -> PolicyDecision:
        """判定一次工具调用是否被允许。"""
        permissions = self.permissions_for(principal)

        if risk_level == RiskLevel.FORBIDDEN:
            return PolicyDecision(allowed=False, reason="forbidden risk level")

        if risk_level == RiskLevel.HIGH:
            if not approved:
                return PolicyDecision(
                    allowed=False,
                    requires_approval=True,
                    reason="human approval required",
                )
            if "tool:high" not in permissions:
                return PolicyDecision(
                    allowed=False,
                    reason=f"principal {principal.subject!r} lacks tool:high permission",
                )
            return PolicyDecision(allowed=True, reason="approved high-risk action")

        if risk_level == RiskLevel.MEDIUM and "tool:medium" not in permissions:
            return PolicyDecision(allowed=False, reason="principal lacks medium-risk permission")

        if risk_level == RiskLevel.LOW and "tool:read" not in permissions:
            return PolicyDecision(allowed=False, reason="principal lacks tool permission")

        return PolicyDecision(allowed=True, reason="policy passed")

    def evaluate_approval(self, principal: Principal) -> PolicyDecision:
        """判定 principal 是否有权批准/拒绝一次挂起的审批。"""
        if "approval:decide" not in self.permissions_for(principal):
            return PolicyDecision(
                allowed=False,
                reason=f"principal {principal.subject!r} lacks approval:decide permission",
            )
        return PolicyDecision(allowed=True, reason="approval permission granted")

    def describe(self) -> dict[str, list[str]]:
        """供 ``GET /policies`` 端点展示当前 RBAC 矩阵。"""
        return {role: sorted(permissions) for role, permissions in sorted(ROLE_PERMISSIONS.items())}


__all__ = ["ROLE_PERMISSIONS", "PolicyEngine"]
