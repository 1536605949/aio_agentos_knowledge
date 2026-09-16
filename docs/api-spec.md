# API 参考

> 代码位置：[`api/app.py`](../api/app.py)（端点）、[`api/schemas.py`](../api/schemas.py)（契约）
> 交互式文档：`GET /docs`（Swagger UI）、`GET /openapi.json`

所有出入参都是 Pydantic 模型，因此 **OpenAPI 文档自动生成且永远与实现一致**。

---

## 目录

| 分组 | 端点数 | 端点 |
|---|---|---|
| [故障处置](#一故障处置) | 4 | `POST /alarm`、`GET /workflow/{id}`、`GET /workflows`、`POST /approval/{id}` |
| [可观测](#二可观测) | 3 | `GET /workflow/{id}/trace`、`/trace/tree`、`GET /metrics` |
| [自描述](#三自描述) | 8 | `/healthz`、`/ontology`、`/skills`、`/agents`、`/tools`、`/policies`、`/router/route`、`/router/plan` |
| [闭环](#四闭环) | 3 | `POST /feedback`、`GET /badcases`、`GET /badcases/summary` |
| [协议](#五协议) | 2 | `GET /.well-known/agent-card.json`、`GET /mcp/tools` |

---

## 通用约定

### 鉴权

| 配置 | 效果 |
|---|---|
| `AIO_AGENTOS_API_KEY` 已设置 | **所有**端点要求 `X-API-Key` 头，不匹配返回 `401` |
| `AIO_AGENTOS_API_KEY` 未设置 | 端点放行（本地开发），但 `/healthz` 的 `security_warnings` 会列出风险 |
| `AIO_AGENTOS_APPROVER_KEY` 已设置 | 审批端点**额外**要求 `X-Approver-Key`，不匹配返回 `403` |

### 限流

仅作用于**写端点**（`POST /alarm`、`POST /approval/{id}`、`POST /feedback`），
避免读端点被轮询打爆。按调用方隔离（`X-API-Key` 优先，缺省回落客户端 IP）：

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 2
```

限流事件会写 BadCase（`category: rate_limited`）。

### 错误语义

| 状态码 | 含义 |
|---|---|
| `401` | `X-API-Key` 缺失或不匹配 |
| `403` | 审批端点缺 `X-Approver-Key`；或审批主体无 `approval:decide` 权限 |
| `404` | 工作流不存在；或路由意图无匹配 Skill |
| `409` | 状态冲突（对非 `waiting_approval` 的工作流提交审批） |
| `422` | 请求体校验失败（Pydantic） |
| `429` | 触发限流 |

---

## 一、故障处置

### `POST /alarm` —— 启动一次故障处置

启动工作流并立即返回状态快照。推理在后台任务中进行，通过 `GET /workflow/{id}` 轮询。

**请求体**（`AlarmRequest`）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `alarm_id` | string | ✅ | — | 告警 ID |
| `service` | string | ✅ | — | 受影响服务名 |
| `severity` | string | | `critical` | 告警严重级别 |
| `message` | string | | `""` | 告警描述 |
| `logs` | string[] | | `[]` | 日志行，作为取证输入 |
| `topology` | object | | `{}` | 可选拓扑上下文（缺省时由 `topology.lookup` 工具采集） |
| `intent` | string | | `null` | 业务意图；缺省使用默认意图 `diagnose` |
| `idempotency_key` | string | | `null` | 幂等键；缺省使用 `alarm_id` |

**请求示例**

```bash
curl -X POST localhost:8000/alarm \
  -H 'Content-Type: application/json' \
  -d '{
    "alarm_id": "A-1001",
    "service": "checkout",
    "severity": "critical",
    "message": "checkout latency spike, 5xx rate 12%",
    "logs": ["upstream timeout while calling payment"],
    "idempotency_key": "alarm-A-1001"
  }'
```

**响应** `202 Accepted` → [`WorkflowResponse`](#workflowresponse)

```json
{
  "workflow_id": "0f1c...",
  "status": "pending",
  "trace_id": "9a3b...",
  "intent": "diagnose",
  "severity": null,
  "root_cause": null,
  "confidence": null,
  "proposed_action": null,
  "action_tool": null,
  "approval_required": false,
  "approved": null,
  "approved_by": null,
  "rejected_by": null,
  "remediation_result": null,
  "ontology_errors": [],
  "steps": [],
  "error": null,
  "created_at": "2026-09-16T12:00:00Z",
  "updated_at": "2026-09-16T12:00:00Z"
}
```

**幂等语义**：`idempotency_key`（或 `alarm_id`）已存在时**不创建新工作流**，直接返回既有状态快照。
幂等映射落 `DocumentStore`，因此**进程重启后仍然生效**。

---

### `GET /workflow/{workflow_id}` —— 读取工作流状态

| 参数 | 位置 | 说明 |
|---|---|---|
| `workflow_id` | path | 工作流 ID |

**响应** `200` → `WorkflowResponse` ｜ `404` 不存在

进程内没有该状态时，会从 `DocumentStore` 回读——**跨重启可查**。

---

### `GET /workflows` —— 列出工作流

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | `50` | 1 ≤ limit ≤ 500 |

**响应** `200` → `WorkflowResponse[]`（按更新时间倒序）

---

### `POST /approval/{workflow_id}` —— 提交审批决定

仅当工作流处于 `waiting_approval` 时可调用。

**请求体**（`ApprovalRequest`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approved` | boolean | ✅ | 批准 / 拒绝 |
| `approver` | string | ✅ | 审批人标识 |
| `comment` | string | | 备注 |

**请求示例**

```bash
curl -X POST localhost:8000/approval/{id} \
  -H 'Content-Type: application/json' \
  -H 'X-Approver-Key: <approver-key>' \
  -d '{"approved": true, "approver": "oncall@example.com"}'
```

**响应** `200` → `WorkflowResponse`

批准后工作流转为 `running` 并立即执行受治理的修复动作；拒绝则转 `rejected`。

**状态码**

| 码 | 场景 |
|---|---|
| `200` | 审批已受理 |
| `403` | 审批主体无 `approval:decide` 权限（`PermissionError`） |
| `404` | 工作流不存在 |
| `409` | 工作流不在 `waiting_approval` 状态（`ValueError`） |

**职责分离**：审批主体固定构造为 `Principal(subject=approver, roles={"approver"})`——
`approver` 有 `approval:decide` 但**没有** `tool:high`，因此审批者无法自己执行高风险动作。
真正执行的是独立的 `workflow-executor` 身份。

**超时默认拒绝**：超过 `AIO_AGENTOS_APPROVAL_TIMEOUT_SECONDS` 未审批，
工作流转 `timed_out`，`approved=False`，`error="approval timed out; default deny"`，
并写 BadCase（`category: approval_timeout`）。

---

### `WorkflowResponse`

所有故障处置端点共用的状态快照。

| 字段 | 类型 | 说明 |
|---|---|---|
| `workflow_id` | string | 工作流 ID |
| `status` | enum | `pending` / `running` / `waiting_approval` / `completed` / `rejected` / `failed` / `timed_out` |
| `trace_id` | string | Trace ID，用于 `POST /feedback` |
| `intent` | string | 路由后的意图，默认 `diagnose` |
| `severity` | string \| null | 归一化后的严重级别（受本体词表约束） |
| `root_cause` | string \| null | 根因（受本体词表约束） |
| `confidence` | number \| null | 0–1；`undetermined` 时上限 0.3 |
| `proposed_action` | string \| null | 建议动作（受本体词表约束） |
| `action_tool` | string \| null | 动作绑定的受治理工具；`null` 表示无副作用动作 |
| `approval_required` | boolean | 是否因工具风险等级需要人工审批 |
| `approved` | boolean \| null | 审批结果 |
| `approved_by` / `rejected_by` | string \| null | 审批 / 拒绝主体 |
| `remediation_result` | object \| null | 工具执行返回体 |
| `ontology_errors` | string[] | 本体公理校验错误；非空即工作流失败 |
| `steps` | string[] | 已执行的图节点顺序 |
| `error` | string \| null | 失败原因（`类型: 消息`） |
| `created_at` / `updated_at` | datetime | 时间戳 |

**状态机**（非法迁移直接抛错，不会静默改写）

```text
pending ──▶ running ──┬──▶ waiting_approval ──┬──▶ running ──▶ completed
                      │                      ├──▶ rejected
                      │                      └──▶ timed_out
                      ├──▶ completed
                      └──▶ failed
```

---

## 二、可观测

### `GET /workflow/{id}/trace` —— 完整 Span 列表

**响应** `200`

```json
{
  "trace_id": "9a3b...",
  "summary": {
    "trace_id": "9a3b...",
    "span_count": 14,
    "root_span_count": 2,
    "error_span_count": 0,
    "max_depth": 4,
    "total_duration_ms": 6.2,
    "by_name": {"agent.run": 5, "llm.complete": 3, "tool.invoke": 2, "graph.run": 1, "workflow.run": 1}
  },
  "spans": [
    {
      "span_id": "...",
      "trace_id": "...",
      "parent_span_id": null,
      "name": "workflow.run",
      "kind": "workflow",
      "started_at": "...",
      "ended_at": "...",
      "duration_ms": 12.4,
      "status": "ok",
      "attributes": {"workflow_id": "...", "intent": "diagnose"},
      "error": null
    }
  ]
}
```

`kind` 取值：`workflow` / `activity` / `agent` / `graph_node` / `tool` / `llm` / `governance`。

`llm.complete` Span 的 `attributes` 里带 `prompt_version`、`provider`、`total_tokens`、`fallback_used`——
**"哪次输出由哪版提示词、哪个 provider 产生"是可查的**。

进程内没有该 Trace 时会从 `DocumentStore` 的 `traces` 归档回读。

---

### `GET /workflow/{id}/trace/tree` —— 嵌套调用树

与上一个端点的区别：`spans` 是扁平列表，本端点返回**已按父子关系嵌套**的结构，前端可直接渲染。

**响应** `200`

```json
{
  "trace_id": "9a3b...",
  "summary": { "span_count": 13, "max_depth": 4, "...": "..." },
  "tree": [
    {
      "name": "workflow.run",
      "kind": "workflow",
      "duration_ms": 12.4,
      "children": [
        {
          "name": "graph.run",
          "kind": "graph_node",
          "children": [
            {
              "name": "agent.run",
              "kind": "agent",
              "attributes": {"agent": "alarm", "capability": "alarm_normalization"},
              "children": [{"name": "llm.complete", "kind": "llm", "children": []}]
            }
          ]
        }
      ]
    }
  ]
}
```

`aio-agentos demo` 就是用这个结构渲染缩进调用树。

---

### `GET /metrics` —— 指标快照

**响应** `200`

```json
{
  "counters": {
    "aio_agentos_workflow_started{intent=diagnose}": 1,
    "aio_agentos_workflow_status{status=running}": 1,
    "aio_agentos_workflow_finished{status=completed}": 1,
    "aio_agentos_tool_invocations{tool=topology.lookup}": 1
  },
  "gauges": {},
  "latency_ms": {
    "aio_agentos_workflow_duration_ms{intent=diagnose}": {"count": 1, "p50": 12.4, "p95": 12.4, "max": 12.4, "avg": 12.4},
    "aio_agentos_tool_latency_ms{tool=service.restart}": {"count": 1, "p50": 0.4, "p95": 0.4, "max": 0.4, "avg": 0.4}
  },
  "llm": { "primary_provider": "deterministic", "calls": 3, "fallback_used": 0, "fallback_rate": 0.0, "total_tokens": 0 },
  "cache": { "hits": 0, "misses": 3, "evictions": 0, "expirations": 0, "size": 1, "hit_rate": 0.0 },
  "rate_limit": { "api:anonymous": {"tokens": 29.0, "capacity": 30, "acquired": 1, "rejected": 0} },
  "resource_locks": { "service:checkout": {"acquisitions": 1, "timeouts": 0, "current_holders": 0, "queued": 0} },
  "tools": {
    "service.restart": {
      "risk_level": 3, "invocations": 1, "failures": 0, "retries": 0,
      "cache_hits": 0, "idempotent_hits": 0, "policy_denials": 0, "rate_limited": 0,
      "consecutive_failures": 0, "circuit_open": false, "p95_latency_ms": 0.1
    }
  },
  "badcases": { "total": 0, "unresolved": 0, "by_category": {}, "by_source": {} }
}
```

**重点关注**：

- `llm.fallback_rate > 0` —— 主 provider 正在系统性失败，但业务看起来完全正常
- `cache.hit_rate` —— 长期为 0 说明缓存 key 设计有问题
- `tools.*.circuit_open` —— 有工具被熔断
- `tools.*.policy_denials` —— **有人在试图越权**
- `resource_locks.*.queued > 0` —— 有资源竞争

---

## 三、自描述

这一组端点把"系统是什么、能做什么、有哪些约束"变成**机器可读的运行时事实**。

### `GET /healthz` —— 健康检查与安全自检

**响应** `200` → `HealthResponse`

```json
{
  "status": "ok",
  "environment": "development",
  "llm_provider": "deterministic",
  "store_backend": "memory",
  "ontology_version": "3.5.0",
  "skills": 6,
  "tools": 9,
  "agents": 6,
  "security_warnings": [
    "AIO_AGENTOS_API_KEY 未配置：所有 API 端点将拒绝请求",
    "AIO_AGENTOS_APPROVER_KEY 未配置：审批端点将拒绝请求"
  ]
}
```

`security_warnings` 非空**不影响 `status`**——它是风险提示而非健康故障。
未配置密钥时系统仍可启动，但风险被显式列出，而不是静默放行。

### `GET /ontology` —— 只读 TBox

```json
{
  "version": "3.5.0",
  "compatible_from": "3.0.0",
  "notes": "AIOps 故障根因分析与受治理修复领域的基线本体",
  "classes": ["Agent", "Alarm", "Evidence", "Host", "Incident", "RemediationAction", "RootCause", "Service", "Skill", "Tool"],
  "properties": ["actionName", "confidence", "incidentId", "riskLevel", "rootCauseKind", "serviceName", "severity", "timeoutSeconds"],
  "relations": ["CAUSED_BY", "DEPENDS_ON", "HAS_CAPABILITY", "LOCATED_ON", "RESOLVED_BY", "USES_TOOL"],
  "axioms": [
    {"name": "incident_id_required", "kind": "required_field"},
    {"name": "root_cause_in_vocabulary", "kind": "allowed_value"},
    {"name": "severity_in_vocabulary", "kind": "allowed_value"},
    {"name": "high_risk_requires_approval", "kind": "high_risk_requires_approval"}
  ],
  "counts": {"classes": 10, "properties": 8, "relations": 6, "axioms": 4}
}
```

### `GET /skills` —— 能力与技能目录

```json
{
  "capabilities": [{"name": "root_cause_reasoning", "description": "基于证据推断故障根因"}],
  "skills": [
    {
      "name": "infer_root_cause",
      "version": "1.0.0",
      "description": "根因推理：在受本体词表约束的取值空间内给出根因与置信度",
      "intents": ["diagnose", "root_cause", "reason", "根因", "诊断"],
      "required_capability": "root_cause_reasoning",
      "allowed_agents": ["reasoning"],
      "allowed_tools": [],
      "risk_level": 1,
      "workflow_template": "incident_response"
    }
  ],
  "intents": ["alarm", "dependency", "diagnose", "fix", "logs", "..." ]
}
```

### `GET /agents` —— Agent 规格

```json
{
  "agents": [
    {
      "name": "reasoning",
      "capabilities": ["root_cause_reasoning"],
      "skills": ["infer_root_cause"],
      "tool_permissions": [],
      "memory_policy": {"read_types": ["short", "episodic"], "write_types": ["short"]},
      "evaluation_metrics": [
        {"name": "schema_validity", "target": 1.0},
        {"name": "ontology_conformance", "target": 1.0}
      ]
    }
  ]
}
```

### `GET /tools` —— 受治理工具清单

```json
{
  "tools": [
    {
      "name": "service.restart",
      "version": "1.0.0",
      "description": "重启不健康的服务实例（高风险，需人工审批）",
      "risk_level": 3,
      "required_permission": "tool:high",
      "timeout_seconds": 5.0,
      "max_retries": 2,
      "cacheable": false,
      "idempotent": true,
      "circuit_open": false,
      "input_schema": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]}
    }
  ]
}
```

### `GET /policies` —— RBAC 矩阵

```json
{
  "roles": {
    "admin": ["approval:decide", "tool:high", "tool:medium", "tool:read"],
    "approver": ["approval:decide", "tool:medium", "tool:read"],
    "operator": ["tool:medium", "tool:read"],
    "viewer": ["tool:read"]
  }
}
```

**注意 `approver` 没有 `tool:high`**——这是职责分离的机械保证。

### `GET /router/route` —— 意图路由

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `intent` | string | ✅ | 业务意图，如 `diagnose` / `remediate` / `ticket` |

**响应** `200` → `RouteResponse` ｜ `404` 无匹配 Skill

```json
{
  "intent": "diagnose",
  "skill": "infer_root_cause",
  "capability": "root_cause_reasoning",
  "agent": "reasoning",
  "tools": [],
  "workflow_template": "incident_response"
}
```

### `GET /router/plan` —— 端到端技能链

| 参数 | 类型 | 默认 |
|---|---|---|
| `intent` | string | `diagnose` |

**响应** `200` → `RouteResponse[]`，返回处理一次故障所需的完整技能链：

```text
normalize_alarm → collect_logs → infer_root_cause → remediate_incident
```

---

## 四、闭环

### `POST /feedback` —— 提交反馈

`rating <= 2` 或 `bad_case=true` 时**自动升级为 BadCase**。

**请求体**（`FeedbackRequest`）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `trace_id` | string | ✅ | — | 对应工作流的 Trace ID |
| `rating` | int | ✅ | — | 1–5 |
| `comment` | string | | `""` | 备注 |
| `bad_case` | boolean | | `false` | 显式标记为坏例 |
| `workflow_id` | string | | `null` | 关联工作流 |
| `submitted_by` | string | | `anonymous` | 提交人 |

**响应** `200` → `FeedbackResponse`

```json
{"accepted": true, "bad_case_id": "c1a2...", "message": "feedback escalated to bad case"}
```

未升级时 `bad_case_id` 为 `null`，`message` 为 `feedback recorded; not a bad case`。

### `GET /badcases` —— BadCase 列表

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | `50` | 1 ≤ limit ≤ 500 |
| `category` | enum | — | 按分类过滤 |
| `unresolved_only` | boolean | `false` | 只看未解决的 |

**响应** `200` → `BadCaseResponse[]`（按创建时间倒序）

```json
[
  {
    "id": "c1a2...",
    "category": "policy_denied",
    "source": "service.restart",
    "message": "human approval required",
    "workflow_id": "0f1c...",
    "trace_id": "9a3b...",
    "resolved": false,
    "created_at": "2026-09-16T12:00:01Z"
  }
]
```

**`category` 取值**（10 类）

| 值 | 来源 |
|---|---|
| `tool_error` | 工具最终失败 |
| `invalid_parameter` | 参数非法 |
| `output_anomaly` | 输出异常 |
| `policy_denied` | 策略/权限拒绝 |
| `ontology_violation` | 本体公理校验失败 |
| `approval_rejected` | 审批被拒 |
| `approval_timeout` | 审批超时 |
| `negative_feedback` | 用户负反馈 |
| `llm_fallback` | LLM 降级 |
| `rate_limited` | 被限流（含资源竞争） |

### `GET /badcases/summary` —— BadCase 汇总

```json
{
  "total": 3,
  "unresolved": 3,
  "by_category": {"approval_timeout": 1, "policy_denied": 2},
  "by_source": {"service.restart": 2, "approval": 1}
}
```

`by_source` 按出现次数倒序——**哪个环节最容易出问题一眼可见**。

---

## 五、协议

### `GET /.well-known/agent-card.json` —— A2A 能力发现

从 Agent 注册表与技能目录**动态生成**，不是静态文件。

```json
{
  "name": "aio-agentos",
  "version": "3.5.0",
  "description": "AIOps 故障根因分析与受治理修复的多 Agent 运行时",
  "endpoint": "http://localhost:8000",
  "capabilities": ["alarm_normalization", "log_evidence", "remediation", "root_cause_reasoning", "ticketing", "topology_evidence"],
  "skills": ["collect_logs", "collect_topology", "create_ticket", "infer_root_cause", "normalize_alarm", "remediate_incident"],
  "auth_schemes": ["none"],
  "input_modes": ["application/json"],
  "output_modes": ["application/json"]
}
```

配置了 `AIO_AGENTOS_API_KEY` 时 `auth_schemes` 变为 `["api-key"]`。

### `GET /mcp/tools` —— MCP 工具目录

从工具注册表生成，供外部 MCP 客户端发现本系统可调用的工具。

```json
{
  "tools": [
    {
      "name": "topology.lookup",
      "description": "查询服务的上下游依赖，为根因推断提供拓扑上下文",
      "version": "1.0.0",
      "input_schema": {"type": "object", "properties": {"service": {"type": "string"}}},
      "output_schema": {}
    }
  ]
}
```

---

## 完整调用示例

```bash
# 1. 启动一次故障处置（幂等）
WF=$(curl -s -X POST localhost:8000/alarm \
  -H 'Content-Type: application/json' \
  -d '{"alarm_id":"A-1001","service":"checkout","severity":"critical",
       "logs":["upstream timeout while calling payment"],
       "idempotency_key":"alarm-A-1001"}' | python -c 'import sys,json;print(json.load(sys.stdin)["workflow_id"])')

# 2. 查看状态（高风险动作会停在 waiting_approval）
curl -s localhost:8000/workflow/$WF | python -m json.tool

# 3. 查看嵌套调用树
curl -s localhost:8000/workflow/$WF/trace/tree | python -m json.tool

# 4. 审批
curl -s -X POST localhost:8000/approval/$WF \
  -H 'Content-Type: application/json' \
  -d '{"approved":true,"approver":"oncall@example.com"}' | python -m json.tool

# 5. 提交负反馈（自动升级为 BadCase）
curl -s -X POST localhost:8000/feedback \
  -H 'Content-Type: application/json' \
  -d '{"trace_id":"<trace_id>","rating":1,"comment":"根因判断不准"}'

# 6. 看闭环汇总
curl -s localhost:8000/badcases/summary | python -m json.tool
```

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_api.py`](../tests/test_api.py) | 1 | 基础端点 |
| [`tests/test_api_extended.py`](../tests/test_api_extended.py) | 18 | 20 个端点、嵌套调用树、审批前后风险对比、拒绝、反馈、鉴权、限流 |

```bash
pytest tests/test_api.py tests/test_api_extended.py -q
```

---

## 未交付

- **分页游标**：`/workflows` 与 `/badcases` 只支持 `limit`，没有 `offset` / `cursor`。
- **Webhook / 事件推送**：状态变化只能轮询，没有回调机制。
- **SSE / WebSocket 流式 Trace**：Trace 只能执行后查询。
- **Prometheus exposition format**：`/metrics` 返回 JSON 快照，需要一个薄适配层才能被直接 scrape。
- **批量端点**：一次只能提交一个告警。
