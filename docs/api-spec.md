# API 契约

## `POST /alarm`

启动故障 Workflow。`alarm_id` 必填；`idempotency_key` 可选，缺省使用 `alarm_id`。重复 key 返回已有 Workflow。

请求示例：

```json
{
  "alarm_id": "A-1001",
  "service": "checkout",
  "severity": "critical",
  "message": "latency spike",
  "logs": ["upstream timeout"],
  "idempotency_key": "incident-A-1001"
}
```

返回 HTTP 202，包含 `workflow_id`、`status`、`trace_id`。

## `GET /workflow/{id}`

读取持久化 Workflow 状态。不存在返回 404。

## `POST /approval/{id}`

仅当 Workflow 为 `waiting_approval` 时可调用。调用者必须包含 `approval:decide` 对应角色；无权限 403，状态冲突 409。

## `GET /workflow/{id}/trace`

返回 Workflow 的 Trace/Span。

## 鉴权

如配置 `AIO_AGENTOS_API_KEY`，所有端点要求 `X-API-Key`。如配置 `AIO_AGENTOS_APPROVER_KEY`，审批端点还要求 `X-Approver-Key`。生产环境应替换为 OIDC/mTLS，并由可信网关传递已验证 principal/role；不得让请求体自报角色。
