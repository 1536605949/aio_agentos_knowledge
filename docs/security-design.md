# Security / Governance

## 风险分级

| 等级 | 定义 | 默认处理 |
|---|---|---|
| LOW | 只读、无状态变更 | 具备 `tool:read` 可执行 |
| MEDIUM | 可逆、局部变更 | 需要 operator 以上权限 |
| HIGH | 服务重启、配置变更、可能影响可用性 | Policy + 人工审批；默认拒绝 |
| FORBIDDEN | 明确禁止自动化的动作 | 永不执行 |

## RBAC

- `viewer`: `tool:read`
- `operator`: `tool:read`, `tool:medium`
- `approver`: `tool:read`, `tool:medium`, `approval:decide`
- `admin`: 以上全部 + `tool:high`

## HITL

```text
Agent proposes action
  -> Tool Registry
  -> Policy Engine
  -> if HIGH: Workflow WAITING_APPROVAL
  -> approval signal
  -> Tool Registry re-checks policy with approval
  -> execute
```

审批 24 小时超时默认拒绝。拒绝/超时都会写 Episodic Memory。生产环境可改成“升级审批人”，但不能自动放行高风险动作。
