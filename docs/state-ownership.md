# 状态所有权

> **改代码前必读。** 本项目有三套状态对象，它们的生命周期与写权限完全不同。
> 混用会导致"审批状态被内存覆盖""重启后幂等失效"这类难查的缺陷。

---

## 一、三套状态，各归谁管

| 状态对象 | 类型 | 位置 | 生命周期 | 是否持久化 |
|---|---|---|---|---|
| `WorkflowState` | 持久化编排状态 | `temporal/models.py` | 小时～天（跨进程、跨重启） | ✅ 每次迁移落库 |
| `AgentContext` | 单次 Agent 调用上下文 | `runtime/context.py` | 一次 `agent.run()` | ❌ 内存 |
| `IncidentGraphState` | 单次图执行中间态 | `graph/state.py` | 一次推理 Activity | ❌ 内存 |

**唯一事实源原则**：一个字段只能有一个持久化事实源。
`AgentContext`、`IncidentGraphState`、Memory **都不允许反向覆盖** `WorkflowState` 的字段。

---

## 二、字段级归属表

| 字段 / 数据 | 唯一事实源 | 谁可以写 | 写入时机 |
|---|---|---|---|
| `workflow_id`、`status` | `WorkflowState` | `IncidentWorkflowService._transition` | 每次状态迁移（经 `assert_transition` 校验） |
| `root_cause`、`confidence` | `WorkflowState` | `_apply_result` | 推理图返回后 |
| `severity`、`topology`、`evidence` | `WorkflowState` | `_apply_result` | 推理图返回后 |
| `proposed_action`、`action_tool` | `WorkflowState` | `_apply_result` | 推理图返回后 |
| `approval_required` | `WorkflowState` | `_apply_result` | 由**工具风险等级**推导，非启发式 |
| `approved`、`approved_by`、`rejected_by` | `WorkflowState` | `approve()` | 审批信号到达时 |
| `ontology_errors` | `WorkflowState` | `_apply_result` | Supervisor 节点输出 |
| `remediation_result` | `WorkflowState` | `_execute_remediation` | 工具执行返回后 |
| `badcase_ids` | `WorkflowState` | `_capture` | BadCase 归档时 |
| `trace_summary` | `WorkflowState` | `_record_duration` | 一次运行结束时 |
| `idempotency_key` → `workflow_id` | `DocumentStore` 的 `workflow_idempotency` 集合 | `start()` | 工作流创建时 |
| Agent 输入输出 | `AgentContext` | 当前 Agent | 调用开始 / 结束 |
| 图中间结果 | `IncidentGraphState` | 当前节点 | 节点迁移 |
| Short / Long / Vector Memory | `InMemoryMemoryStore` | Memory service | 显式写入事件 |
| Incident episode | `InMemoryMemoryStore` | `_write_episode` | `completed` / `rejected` / `timed_out` / `failed` |
| Span / Trace | `DocumentStore` 的 `traces` 集合 | `AgentTrace` + `_persist` | 每个 Span 关闭时（含异常路径） |
| BadCase | `DocumentStore` 的 `badcases` 集合 | `BadCaseCollector` | 各层捕获时 |
| 工具幂等结果 | `DocumentStore` 的 `tool_idempotency` 集合 | `ToolRegistry` | 首次成功执行后 |

---

## 三、四条红线

### 红线 1：不用 Memory 重建编排状态

```python
# ❌ 错误：从记忆里恢复审批状态
episode = memory.list(workflow_id)[-1]
if episode.content["approved"]:
    execute()

# ✅ 正确：读持久化的工作流状态
state = service.get(workflow_id)
if state.approved:
    execute()
```

Memory 是**派生数据**，用于检索与经验复用，不是编排状态数据库。

### 红线 2：`AgentContext` 不跨 Agent 调用复用

```python
# ❌ 错误：一个 context 传给多个 Agent
ctx = AgentContext(workflow_id=..., agent_name="log", inputs={...})
await log_agent.run(ctx, trace)
await reasoning_agent.run(ctx, trace)   # agent_name 还是 "log"！

# ✅ 正确：每个 Agent 一个 context（graph/nodes.py::GraphDeps.context）
log_ctx = deps.context("log", {"logs": ...})
reasoning_ctx = deps.context("reasoning", {"evidence": ...})
```

`AgentContext` 承载 `agent_name`、`lifecycle` 与 `outputs`，跨 Agent 复用会让生命周期状态机失去意义。

### 红线 3：状态迁移必须走显式转移表

```python
# temporal/models.py
ALLOWED_TRANSITIONS = {
    WorkflowStatus.PENDING:          frozenset({RUNNING, FAILED}),
    WorkflowStatus.RUNNING:          frozenset({WAITING_APPROVAL, COMPLETED, FAILED}),
    WorkflowStatus.WAITING_APPROVAL: frozenset({RUNNING, REJECTED, TIMED_OUT, FAILED}),
    WorkflowStatus.COMPLETED:        frozenset(),   # 终态
    ...
}

def assert_transition(current, target):
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid workflow transition: {current.value} -> {target.value}")
```

```python
# ❌ 错误：静默改写状态
state.status = WorkflowStatus.COMPLETED

# ✅ 正确
self._transition(state, WorkflowStatus.COMPLETED)
```

`_transition` 同时负责落库与指标上报，绕过它就等于绕过持久化。

### 红线 4：Trace 不参与业务判定

```python
# ❌ 错误：根据 Span 里的错误判断要不要重试
if trace.failed_spans():
    retry()

# ✅ 正确：业务判定看 WorkflowState 与异常
```

Trace 只用于诊断。它的写入是"尽力而为"的（虽然异常路径也保证落盘），不能作为控制流的依据。

---

## 四、持久化的边界在哪

```text
┌──────────────────────── 持久化（DocumentStore）────────────────────────┐
│  workflows           WorkflowState 快照                                │
│  traces              Span 归档（支持重启后回读完整调用树）                 │
│  workflow_idempotency  API 层幂等键 → workflow_id                       │
│  tool_idempotency    工具级幂等结果（保证副作用只发生一次）                │
│  badcases            失败样本                                          │
└───────────────────────────────────────────────────────────────────────┘

┌──────────────────────── 非持久化（进程内）─────────────────────────────┐
│  AgentContext         单次 Agent 调用上下文                             │
│  IncidentGraphState   单次图执行中间态                                  │
│  InMemoryMemoryStore  Short/Long/Vector/Episodic 记录（默认实现）        │
│  AgentTrace 的活跃 Span 栈（关闭时立即归档到 traces）                     │
└───────────────────────────────────────────────────────────────────────┘
```

切到 SQLite 后端即可获得跨重启的完整恢复能力：

```bash
export AIO_AGENTOS_STORE_BACKEND=sqlite
export AIO_AGENTOS_SQLITE_PATH=aio_agentos.db
```

验证用例：`tests/test_workflow_lifecycle.py::test_state_is_persisted_and_readable_by_a_fresh_service`
——新建一个 `states` / `traces` 全空的服务实例，仍能读到状态、读到完整调用树、并完成审批闭环。

---

## 五、为什么审批状态必须持久化

这是最容易出事的地方。假设审批状态只在内存里：

```text
t0  工作流进入 WAITING_APPROVAL（内存标记）
t1  进程重启（发布、OOM、扩缩容）
t2  审批人点"批准"
t3  内存里没有这个工作流 → 审批丢失 → 故障没人处理
```

或者更糟：如果实现成"重启后默认放行"，高风险动作会在无人审批的情况下执行。

本项目的处理：

- `WorkflowState` 每次迁移都落库 → 重启后 `service.get(workflow_id)` 仍能读到 `WAITING_APPROVAL`。
- 审批超时任务由 `asyncio.Task` 承载，`close()` 时统一取消；在 Temporal 版本里对应 `workflow.wait_condition(timeout=...)`，由服务端持久化定时器保证。
- 超时结果固定为 `TIMED_OUT` + `approved=False`（**默认拒绝**），并有专门的 BadCase 分类 `APPROVAL_TIMEOUT`。

---

## 六、相关代码

| 关注点 | 位置 |
|---|---|
| 状态定义与转移表 | `temporal/models.py` |
| 状态迁移与落库 | `temporal/workflow.py::_transition` / `_persist` |
| 单次 Agent 上下文 | `runtime/context.py` |
| 生命周期状态机 | `runtime/lifecycle.py` |
| 图状态定义 | `graph/state.py` |
| 节点如何构造上下文 | `graph/nodes.py::GraphDeps.context` |
| 记忆记录 | `memory/types.py`、`memory/store.py` |
| 持久化协议 | `persistence/base.py` |

相关测试：`tests/test_workflow_lifecycle.py`（18 个用例覆盖状态机、持久化恢复、职责分离、超时默认拒绝）
