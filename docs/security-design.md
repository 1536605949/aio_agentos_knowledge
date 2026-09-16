# 工具与治理

> 代码位置：[`governance/`](../governance/)、[`tools/`](../tools/)
> 策略引擎：[`governance/policy.py`](../governance/policy.py) ｜ 执行器：[`tools/registry.py`](../tools/registry.py)

> 本文覆盖安全治理、工具契约、审批与职责分离、以及工具层的容错设计。
> 弹性能力（限流/缓存/资源锁/熔断）的实现细节见 [弹性与横切能力](resilience-design.md)。

---

## 一、核心问题

AIOps 场景里，把 LLM 接进故障处置链路最危险的一件事是：

> **模型给出了一个"重启生产数据库"的建议，系统真的去执行了。**

本项目对这个问题给出的答案不是"提示词里写上不要这么做"，而是**四层机械保证**：

| 层 | 保证 | 代码位置 |
|---|---|---|
| 1 | 模型只能在词表内选动作（越界收敛到 `collect_more_evidence`） | `agents/implementations.py` |
| 2 | 审批门由**工具声明的风险等级**推导，不是写死的 if | `tools/catalog.py` |
| 3 | 未审批时策略**直接拒绝**，且拒绝**不被重试掩盖** | `tools/registry.py` |
| 4 | 审批者**没有执行权限**（职责分离） | `governance/policy.py` |

---

## 二、风险分级

```python
class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FORBIDDEN = 4
```

用 `IntEnum` 而非 `StrEnum` 是为了支持数值比较：

```python
requires_approval = int(spec.risk_level) >= 3        # HIGH 及以上
```

| 等级 | 定义 | 默认处理 |
|---|---|---|
| `LOW` | 只读、无状态变更 | 具备 `tool:read` 即可执行 |
| `MEDIUM` | 可逆、局部变更 | 需要 `tool:medium`（operator 及以上） |
| `HIGH` | 服务重启、配置变更、可能影响可用性 | **需要人工审批**；未审批直接拒绝 |
| `FORBIDDEN` | 明确禁止自动化的动作 | **永不执行**，无论谁批准 |

### 当前 9 个工具的风险分布

| 工具 | 风险 | 需要的权限 | 幂等 | 缓存 | 资源锁 |
|---|---|---|---|---|---|
| `topology.lookup` | LOW | `tool:read` | — | ✅ 60s | — |
| `logs.search` | LOW | `tool:read` | — | ✅ | — |
| `dba.escalate` | LOW | `tool:read` | ✅ | — | — |
| `network.escalate` | LOW | `tool:read` | ✅ | — | — |
| `ticket.create` | LOW | `tool:read` | ✅ | — | — |
| `demo.flaky` | LOW | `tool:read` | — | — | — |
| `service.scale_out` | MEDIUM | `tool:medium` | — | — | ✅ `service` |
| `service.restart` | **HIGH** | `tool:high` | ✅ | — | ✅ `service` |
| `config.rollback` | **HIGH** | `tool:high` | ✅ | — | ✅ `service` |

**注意高风险工具全部开了 `idempotent` + `resource_field`**——
这两个声明直接决定 `ToolRegistry` 的调用策略（见第五节）。

---

## 三、RBAC：角色到权限是显式表

```python
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer":   frozenset({"tool:read"}),
    "operator": frozenset({"tool:read", "tool:medium"}),
    "approver": frozenset({"tool:read", "tool:medium", "approval:decide"}),
    "admin":    frozenset({"tool:read", "tool:medium", "tool:high", "approval:decide"}),
}
```

**新增角色只需在此声明一处**，不需要在业务代码里加 `if role == ...`。

```python
def permissions_for(self, principal: Principal) -> set[str]:
    """把 principal 的角色集合展开为权限集合；未知角色不贡献任何权限。"""
    permissions: set[str] = set()
    for role in principal.roles:
        permissions |= ROLE_PERMISSIONS.get(role, frozenset())
    return permissions
```

`ROLE_PERMISSIONS.get(role, frozenset())` —— **未知角色不贡献权限**（fail-closed），
而不是抛错或放行。

`GET /policies` 直接暴露这张矩阵：

```json
{"roles": {"admin": ["approval:decide", "tool:high", "tool:medium", "tool:read"],
           "approver": ["approval:decide", "tool:medium", "tool:read"], ...}}
```

---

## 四、职责分离：一个被修复的越权缺陷

**这是本项目修复的最重要的安全缺陷。**

### 缺陷

早期实现的判定写成了：

```python
# ❌ 错误
if "tool:high" not in permissions and "approval:decide" not in permissions:
    return PolicyDecision(allowed=False, reason="lacks tool:high permission")
```

这是**逻辑与（AND）**：只要主体拥有 `approval:decide`，即使**没有** `tool:high`，也能通过检查。

后果：`approver` 角色的权限集合是 `{tool:read, tool:medium, approval:decide}`，
不包含 `tool:high`——但因为它有 `approval:decide`，条件不成立，于是**判定放行**。

```text
approver 批准了高风险动作
   → 因为 AND 逻辑，approver 自己也获得了执行权
   → 职责分离形同虚设
   → 一次审批 = 批准 + 执行，无独立复核
```

### 修复

```python
# ✅ 正确：只认 tool:high
if "tool:high" not in permissions:
    return PolicyDecision(
        allowed=False,
        reason=f"principal {principal.subject!r} lacks tool:high permission",
    )
```

现在：

| 主体 | 角色 | 权限 | 能否批准 | 能否执行高风险 |
|---|---|---|---|---|
| on-call 工程师 | `approver` | `tool:read`、`tool:medium`、`approval:decide` | ✅ | ❌ |
| 工作流执行器 | `admin` | 含 `tool:high` | — | ✅ |

**批准与执行由两个独立主体完成**：

```python
# temporal/workflow.py::approve
state.approved_by = principal.subject        # 人工主体
self._transition(state, WorkflowStatus.RUNNING)
# 职责分离：人工主体负责批准，受治理的 workflow-executor 身份负责执行。
executor = Principal(subject="workflow-executor", roles={"admin"})
await self._execute_remediation(state, ..., approved=True, principal=executor)
```

### 回归保护

```python
async def test_approver_role_cannot_execute_high_risk_tool():
    """职责分离：审批者只有 approval:decide，没有 tool:high。"""
    registry = ToolRegistry()
    registry.register(_spec("service.restart", risk_level=RiskLevel.HIGH,
                            required_permission="tool:high"), _echo)

    with pytest.raises(ToolPolicyDenied) as excinfo:
        await registry.invoke("service.restart", {}, APPROVER, AgentTrace(), approved=True)
    assert "tool:high" in str(excinfo.value)
```

注意 `approved=True` —— **即使已批准，approver 也不能自己执行**。

---

## 五、工具契约：`ToolSpec` 是唯一声明点

[`tools/models.py`](../tools/models.py)：

```python
class ToolSpec(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=5)
    required_permission: str = "tool:read"
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)

    cacheable: bool = False
    cache_ttl_seconds: float | None = None
    idempotent: bool = False
    resource_field: str | None = None
```

**一个 `ToolSpec` 被三处消费**：

| 消费方 | 读什么字段 | 决定什么 |
|---|---|---|
| 治理层（`PolicyEngine`） | `risk_level`、`required_permission` | 是否需要审批、需要什么权限 |
| 执行层（`ToolRegistry`） | `max_retries`、`timeout_seconds`、`cacheable`、`idempotent`、`resource_field` | 重试/超时/缓存/幂等/锁策略 |
| 协议层（`build_mcp_catalog`） | `name`、`description`、`input_schema`、`output_schema` | MCP 工具描述符 |

**这就是"单一事实源"的价值**：新增一个高风险工具，只需在 `ToolSpec` 里写一次
`risk_level=RiskLevel.HIGH`，审批门、权限校验、MCP 描述符会自动跟上。

---

## 六、10 步调用链

`ToolRegistry.invoke()` 的步骤**顺序即设计**：

```text
ToolRegistry.invoke(name, payload, principal, trace, approved, idempotency_key)
  │
  ├─ 1. 存在性检查        未注册 → ToolNotFound（不做隐式放行）
  ├─ 2. 熔断检查          连续失败达阈值 → CircuitOpen
  │
  ├─ 3. 治理检查  ◀────── 在重试循环之外！
  ├─ 4. 权限检查           required_permission ∈ principal 权限集合
  │
  ├─ 5. 出站限流           令牌桶，按 tool:{name} 隔离
  ├─ 6. 幂等查询           idempotency_key 命中即返回
  ├─ 7. 结果缓存           cacheable 工具按 payload 哈希
  │
  ├─ 8. 资源锁             resource_field 指定的资源互斥
  │     │
  │     └─ 9. 重试循环 ─── 仅对瞬时错误重试
  │           └─ trace.span("tool.invoke", kind=TOOL, attempt=N)
  │
  └─ 10. 失败归档 ─────── 最终失败写 BadCase
```

### 第 3 步的位置是最关键的

```python
async def invoke(self, name, payload, principal, trace, approved=False, idempotency_key=None):
    entry = self._tools.get(name)
    if entry is None:
        raise ToolNotFound(name)
    if entry.circuit_open_until > monotonic():
        raise CircuitOpen(name)

    # --- 3/4. 治理与权限：位于重试循环之外 ---
    decision = self.policy.evaluate_tool(principal, entry.spec.risk_level, approved=approved)
    if not decision.allowed:
        entry.policy_denials += 1
        self._badcase(BadCaseCategory.POLICY_DENIED, name, decision.reason, trace)
        raise ToolPolicyDenied(decision.reason)
    permissions = self.policy.permissions_for(principal)
    if entry.spec.required_permission not in permissions:
        entry.policy_denials += 1
        ...
        raise ToolPolicyDenied(reason)

    # ... 限流 / 幂等 / 缓存 ...
    try:
        async with self._maybe_lock(entry, payload, name):
            for attempt in range(entry.spec.max_retries + 1):     # ← 重试循环从这开始
                ...
```

如果治理检查在循环**内部**，后果是：

```text
策略拒绝「未经审批的高风险动作」
  → 被当成瞬时故障
  → 重试 3 次（每次都重新判定、都拒绝）
  → 最终报 ToolError 而不是 ToolPolicyDenied
  → 运维看到的是"工具不稳定"，而不是"有人试图越权"
```

**这是早期实现里最危险的缺陷**：安全事件被伪装成稳定性问题。

回归保护：

```python
async def test_policy_denial_is_not_masked_by_retries():
    """策略拒绝必须发生在重试循环之外——否则会被当成瞬时故障反复重试。"""
    calls = []
    async def handler(payload):
        calls.append(payload)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(_spec("service.restart", risk_level=RiskLevel.HIGH,
                            required_permission="tool:high"), handler)

    with pytest.raises(ToolPolicyDenied) as excinfo:
        await registry.invoke("service.restart", {}, ADMIN, AgentTrace(), approved=False)
    assert "approval" in str(excinfo.value)
    assert calls == []                                         # ← handler 一次都没被调用
    assert registry.stats()["service.restart"]["policy_denials"] == 1
```

`calls == []` 是核心断言：**策略拒绝时 handler 从未被触达**。

---

## 七、审批流程（HITL）

```text
Agent 提出动作
   │
   ▼
ToolRegistry 判定 requires_approval（来自工具风险等级）
   │
   ▼
WorkflowState.status = WAITING_APPROVAL          ← 持久化落库
   │  同时启动超时任务（默认 24h）
   │
   ├── POST /approval/{id}  approved=true
   │      │
   │      ├─ evaluate_approval(principal)  ← 必须含 approval:decide
   │      ├─ 取消超时任务
   │      ├─ approved_by = principal.subject
   │      ├─ status → RUNNING
   │      └─ executor = Principal("workflow-executor", roles={"admin"})
   │            └─ ToolRegistry.invoke(..., approved=True)  ← 重新过一遍治理
   │
   ├── POST /approval/{id}  approved=false
   │      └─ status → REJECTED + BadCase(APPROVAL_REJECTED) + Episodic Memory
   │
   └── 超时（默认 24h）
          └─ status → TIMED_OUT + approved=False
             error = "approval timed out; default deny"
             + BadCase(APPROVAL_TIMEOUT) + Episodic Memory
```

### 超时默认拒绝

```python
async def _approval_timeout(self, workflow_id: str) -> None:
    await asyncio.sleep(self.approval_timeout_seconds)
    state = self.states.get(workflow_id)
    if state is None or state.status is not WorkflowStatus.WAITING_APPROVAL:
        return
    state.approved = False
    state.error = "approval timed out; default deny"
    self._transition(state, WorkflowStatus.TIMED_OUT)
    self._capture(BadCaseCategory.APPROVAL_TIMEOUT, "approval", state.error, state)
    self._write_episode(state, "approval_timeout")
```

**"没人审批"永远不等于"默认批准"。** 生产环境可以改成"升级到上一级审批人"，
但**不能自动放行高风险动作**。

`close()` 会取消所有挂起的超时任务，避免进程退出时报 `Task was destroyed but it is pending!`。

### 审批状态必须持久化

`WAITING_APPROVAL` 是**唯一会长时间挂起的状态**。如果它只在内存里：

```text
t0  推理完成，动作需要审批 → WAITING_APPROVAL
t1  服务重启（部署 / OOM / 节点迁移）
t2  审批人点击批准 → 系统说"没有这个工作流"
```

状态、`approval_deadline`、`trace_id` 全部落 `DocumentStore`，因此跨重启可继续审批。
详见 [状态所有权](state-ownership.md#为什么审批状态必须持久化)。

---

## 八、重试分级

```python
class TransientToolError(ToolError):
    """瞬时故障（网络抖动、下游 5xx、超时）。**会**被重试。"""

class PermanentToolError(ToolError):
    """永久故障（参数非法、资源不存在、权限不足）。**不会**被重试。"""
```

重试循环里的分级处理：

```python
for attempt in range(entry.spec.max_retries + 1):
    try:
        with trace.span("tool.invoke", kind=SpanKind.TOOL, tool=name, attempt=attempt, ...):
            result = await asyncio.wait_for(entry.handler(payload),
                                           timeout=entry.spec.timeout_seconds)
        entry.consecutive_failures = 0                    # 成功即复位熔断计数
        ...
        return result
    except PermanentToolError as exc:
        last_error = exc
        entry.failures += 1
        entry.consecutive_failures += 1
        break                                             # ← 立即退出，不重试
    except Exception as exc:
        last_error = exc
        entry.failures += 1
        entry.consecutive_failures += 1
        if entry.consecutive_failures >= self.circuit_threshold:
            entry.circuit_open_until = monotonic() + self.cooldown_seconds
            break
        if attempt < entry.spec.max_retries:
            entry.retries += 1
            await asyncio.sleep(min(0.05 * (2**attempt), 0.5))     # 指数退避，上限 0.5s
```

**参数缺失重试 5 次仍然是缺参数**——重试只是浪费时间并放大副作用风险。

`tools/catalog.py` 里的例子：

```python
async def _restart_service(payload):
    service = payload.get("service")
    if not service:
        raise PermanentToolError("service.restart requires a 'service' field")
```

### 幂等：重试不重复执行

```python
key = idempotency_key or (payload.get("idempotency_key") if entry.spec.idempotent else None)
if entry.spec.idempotent and key:
    cached = self._idempotent_lookup(name, str(key))
    if cached is not None:
        entry.idempotent_hits += 1
        return cached                    # handler 不再执行
```

幂等记录存进 `DocumentStore`（collection `tool_idempotency`），**不是进程内 dict**：

```python
def _idempotent_store(self, tool: str, key: str, result: dict) -> None:
    scoped = f"{tool}:{key}"
    if self.store is not None:
        self.store.save(IDEMPOTENCY_COLLECTION, scoped, {"result": result})
        return
    self._idempotent[scoped] = result
```

`test_idempotency_persists_through_store` 用一个**全新的 `ToolRegistry` 实例**
（模拟进程重启后重新装配）验证仍能命中同一幂等记录。

工作流层的幂等键是 `f"{workflow_id}:{tool}"`——同一工作流对同一工具只会真正执行一次。

---

## 九、工具探针（自检）

`aio-agentos check` 会真的把四条关键路径跑一遍：

```python
probes["low_risk_ok"] = await runtime.tools.invoke("topology.lookup", {"service": "checkout"}, viewer, trace)

try:
    await runtime.tools.invoke("service.restart", {"service": "checkout"}, admin, trace, approved=False)
    probes["high_risk_without_approval"] = "BUG: should have been denied"
except Exception as exc:
    probes["high_risk_without_approval"] = f"{type(exc).__name__}: {exc}"

probes["high_risk_with_approval"] = await runtime.tools.invoke(
    "service.restart", {"service": "checkout"}, admin, trace, approved=True)

probes["retry_succeeded"] = await runtime.tools.invoke("demo.flaky", {"fail_times": 2}, viewer, trace)

try:
    await runtime.tools.invoke("service.restart", {"service": ""}, admin, trace, approved=True)
    probes["permanent_error_not_retried"] = "BUG: should have raised"
except Exception as exc:
    probes["permanent_error_not_retried"] = type(exc).__name__
```

实测输出：

```json
{
  "low_risk_ok": {"service": "checkout", "dependencies": ["payment", "inventory", "cart"]},
  "high_risk_without_approval": "ToolPolicyDenied: human approval required",
  "high_risk_with_approval": {"executed": true, "mode": "reference"},
  "retry_succeeded": {"attempts": 3, "ok": true},
  "permanent_error_not_retried": "ToolError",
  "idempotent_replay_same_result": true
}
```

出现 `BUG` 前缀或高风险未被拒，`check` 退出码为 `1`——**可直接作为部署前置门禁**。

---

## 十、生产接入点

当前 `tools/catalog.py` 里所有 handler 都返回 `mode="reference"`，**不产生真实副作用**。
接入真实基础设施只需替换 handler，其余链路（治理/权限/限流/幂等/缓存/锁/重试/BadCase）全部不变：

```python
async def _restart_service(payload):
    service = payload["service"]
    if not service:
        raise PermanentToolError("service.restart requires a 'service' field")
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{K8S_API}/services/{service}/restart", timeout=5)
    if resp.status_code >= 500:
        raise TransientToolError(f"k8s api unavailable: {resp.status_code}")   # 可重试
    if resp.status_code == 404:
        raise PermanentToolError(f"service not found: {service}")               # 不重试
    return resp.json()
```

**关键是把 HTTP 状态码正确映射到两类异常**——这决定了重试行为是否正确。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_governance_tools.py`](../tests/test_governance_tools.py) | 1 | 策略与工具基础行为 |
| [`tests/test_tools_resilience.py`](../tests/test_tools_resilience.py) | 17 | 治理位置、审批者越权、重试分级、熔断、幂等 + store、缓存、限流、资源锁、BadCase |
| [`tests/test_workflow_lifecycle.py`](../tests/test_workflow_lifecycle.py) | 16 | 审批超时默认拒绝、职责分离、状态机、持久化恢复 |

```bash
pytest tests/test_governance_tools.py tests/test_tools_resilience.py tests/test_workflow_lifecycle.py -q
```

---

## 未交付

- **真实身份提供方**：`Principal` 由 API 层显式构造，没有集成 OIDC / mTLS。
  当前 `X-API-Key` 是**共享密钥**，不是身份凭证——生产必须由可信网关传递已验证的 principal/role，
  **不得让请求体自报角色**（`POST /approval` 的 `approver` 字段目前是自报的，
  仅在共享密钥的保护下可信）。
- **策略的声明式表达**：`PolicyEngine` 是代码实现，不是 OPA/Rego 这类策略语言。
- **`FORBIDDEN` 等级的实际使用**：枚举值存在且判定逻辑已实现（`if risk_level == RiskLevel.FORBIDDEN: return allowed=False`），
  但当前 9 个工具里**没有任何一个使用该等级**。
- **审批委托与多级审批**：只有单级、单人的批准/拒绝。
- **审批意见留痕**：`ApprovalRequest.comment` 字段存在，但未写入 `WorkflowState`。
- **工具输入 JSON Schema 强制校验**：`input_schema` 是声明，`invoke` 不做 schema 校验，
  参数合法性由 handler 自己用 `PermanentToolError` 表达。
- **密钥轮换**：`AIO_AGENTOS_API_KEY` 是单值，不支持多密钥并行轮换。
