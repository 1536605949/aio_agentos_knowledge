# 分层架构

> 本文档回答三个问题：**有哪些层？谁依赖谁？依赖在哪里装配？**

---

## 一、五层结构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 接入层  api/ · cli.py · temporal/production.py · temporal/sdk_client.py   │
│         职责：协议转换、鉴权、限流。不含业务判断。                          │
├──────────────────────────────────────────────────────────────────────────┤
│ 编排层  temporal/ · graph/                                                │
│         外层：IncidentWorkflowService —— 持久化状态机、审批挂起/恢复        │
│         内层：IncidentReasoningGraph  —— 短时推理图，一次 Activity 内跑完   │
├──────────────────────────────────────────────────────────────────────────┤
│ 领域层  agents/ · skills/ · router/ · tools/ · governance/ · runtime/     │
│         职责：把"意图"解析为"执行契约"，并在治理约束下执行。                 │
├──────────────────────────────────────────────────────────────────────────┤
│ 知识层  ontology/ · memory/                                               │
│         职责：提供只读语义底座与记忆记录。不含执行逻辑。                     │
├──────────────────────────────────────────────────────────────────────────┤
│ 基础层  llm/ · persistence/ · resilience/ · concurrency/ · observability/ │
│         职责：可被任何上层安全引用的通用能力。                              │
└──────────────────────────────────────────────────────────────────────────┘
                     ▲
              所有依赖在此注入
         ┌─────────────────────────┐
         │  bootstrap.py（组装根）   │
         └─────────────────────────┘
```

---

## 二、依赖方向

**只允许向下依赖**（上层依赖下层，下层绝不反向依赖上层）。这条规则由 `import-linter` 在 CI 中强制执行。

```text
api ─────────────┐
cli ─────────────┤
temporal ────────┤
graph ───────────┤
agents ──────────┼──▶ skills ──▶ governance
tools ───────────┤     │            │
router ──────────┘     ▼            ▼
                  ontology      observability
                       │            │
                       └────────────┴──▶ persistence / resilience / concurrency
```

### 固化在 `pyproject.toml` 的 7 条契约

| # | 契约 | 约束 | 为什么 |
|---|---|---|---|
| 1 | `observability` 不依赖领域/编排层 | 禁止依赖 ontology / memory / runtime / governance / tools / skills / router / agents / graph / temporal / protocol / api / llm | 它必须能被任何一层安全引用（包括被 `tools` 引用去记录 BadCase） |
| 2 | `llm` 不依赖领域/编排层 | 禁止依赖 ontology / memory / runtime / governance / tools / … / persistence | 换模型不该牵动业务代码 |
| 3 | `resilience` / `concurrency` 是基础设施叶子 | 禁止依赖几乎所有其他内部包 | 它们是纯机制，不该有业务知识 |
| 4 | `persistence` 不依赖领域/编排层 | 禁止依赖 ontology / … / llm / resilience / concurrency | 存储抽象必须能独立替换 |
| 5 | `ontology` / `memory` 不依赖编排层 | 禁止依赖 graph / temporal / api / llm / tools / agents / router / skills / persistence | 知识层是只读底座 |
| 6 | `protocol` 不被知识层引用 | ontology、memory 不得 import protocol | 协议是对外契约，不该反向污染知识定义 |
| 7 | `governance` 是纯策略层 | 禁止依赖 agents / graph / temporal / api / router / skills / tools | 策略判定必须可独立测试，不被执行细节污染 |

> **说明**：契约 1 刻意允许 `observability → persistence`。BadCase 需要落库，
> 而 `persistence` 是比 `observability` 更低的基础设施，属于合法的向下依赖。

验证方式：

```bash
lint-imports
# Contracts: 7 kept, 0 broken.
```

---

## 三、双层编排：为什么是嵌套而不是流水线

这是本项目最容易被误解的设计点。

```text
      ┌──────────── 外层：持久化壳（可能存活数小时到数天）────────────┐
      │  IncidentWorkflowService                                    │
      │    · WorkflowState 落库，每次状态迁移都持久化                  │
      │    · WAITING_APPROVAL 长时间挂起，靠审批信号恢复               │
      │    · 超时 → TIMED_OUT（默认拒绝）                             │
      │                                                            │
      │     ┌──── 内层：短时推理图（毫秒到秒级，无状态）────┐           │
      │     │  IncidentReasoningGraph                     │           │
      │     │    normalize_alarm → collect_topology        │           │
      │     │      → collect_logs → reason → propose       │           │
      │     │        → supervise                          │           │
      │     └─────────────────────────────────────────────┘           │
      └────────────────────────────────────────────────────────────┘
```

| 维度 | 外层（`temporal/`） | 内层（`graph/`） |
|---|---|---|
| 生命周期 | 长（跨进程、跨重启） | 短（一次 Activity） |
| 状态 | `WorkflowState`，**持久化** | `IncidentGraphState`，**内存** |
| 职责 | 编排、审批、超时、重试策略 | 推理、取证、动作规划、本体校验 |
| 是否确定 | 是（Temporal 要求） | 否（含 LLM 调用） |
| 失败处理 | 状态迁移到 `FAILED`，写 BadCase | 抛异常，由外层捕获 |

**为什么不是串行流水线阶段？**

审批等待可能持续数小时，而推理只有几百毫秒。如果把推理放进同一个持久化上下文：

- 要么推理期间一直占用工作流槽位；
- 要么审批恢复后需要重放整个推理过程。

拆成"持久化壳 + 短时内核"后，审批期间只保留一个状态快照，恢复时直接进入执行阶段。

**对应的 Temporal 实现**（`temporal/production.py`）：

- 外层 = `@workflow.defn class IncidentWorkflow`（确定性编排，只做状态迁移与信号等待）
- 内层 = `@activity.defn reasoning_activity`（所有副作用都在这里）
- 审批 = `@workflow.signal approval` + `workflow.wait_condition(timeout=...)`
- 执行 = `@activity.defn remediation_activity`，**治理与幂等仍在 `ToolRegistry` 内部完成**

这样本地参考实现与 Temporal 实现走的是同一条治理路径，不会语义分叉。

---

## 四、一次请求的完整数据流

```text
POST /alarm
  │
  │ api/app.py::create_alarm
  │   ├─ rate_limit 依赖：令牌桶按调用方隔离（仅写端点）
  │   ├─ _require_api_key：配置了密钥就强制校验
  │   └─ service.resolve_idempotency(key) → 命中则直接返回既有工作流
  │
  ▼
temporal/workflow.py::start
  │   ├─ router.route_or_default(intent)   ← 意图 → 技能 → Agent → 工具白名单
  │   ├─ 创建 WorkflowState（含 trace_id）+ AgentTrace
  │   └─ _persist(state)                    ← 落库
  │
  ▼ asyncio.create_task(_run)
  │
  │ _transition(RUNNING) → limiter.acquire("workflow:reasoning")
  │
  │ with trace.span("workflow.run", kind=WORKFLOW)          ← Span 根
  │   └─ graph.run(...)
  │        └─ with trace.span("graph.run", kind=GRAPH_NODE)
  │             ├─ normalize_alarm  → agent.run → llm.complete
  │             ├─ collect_topology → agent.run → tool.invoke
  │             ├─ collect_logs     → agent.run
  │             ├─ reason           → agent.run → llm.complete
  │             ├─ propose          → agent.run → llm.complete
  │             └─ supervise        → graph.supervise（本体公理校验）
  │
  │ _apply_result(state)
  │   ├─ 本体错误？ → FAILED + BadCase(ONTOLOGY_VIOLATION)
  │   ├─ 需审批？   → WAITING_APPROVAL + 启动超时任务
  │   └─ 否则      → _execute_remediation
  │
  ▼
POST /approval/{id}
  │   ├─ policy.evaluate_approval(principal)  ← 必须有 approval:decide
  │   └─ _execute_remediation(approved=True, executor=workflow-executor)
  │        ├─ locks.acquire("service:checkout")   ← FIFO + 超时
  │        └─ tools.invoke(tool, ..., idempotency_key=...)
  │             └─ 治理 → 权限 → 限流 → 幂等 → 缓存 → 资源锁 → 分级重试 → 熔断
  │
  ▼
COMPLETED + Trace（落库） + EpisodicMemory + Metrics + 可能的 BadCase
```

---

## 五、组装根：为什么需要 `bootstrap.py`

早期实现里依赖是"到处 `new`"的：

```python
# 修复前
class InMemoryIncidentWorkflowService:
    def __init__(self, tools, memory=None, approval_timeout_seconds=24 * 3600):
        self.graph = IncidentReasoningGraph()   # ← 自己 new 一个图
        ...
```

问题有三个：

1. 同一份配置被读多次，可能不一致。
2. 限流 / 缓存 / 持久化等横切能力**没有注入点**——所以它们根本不存在。
3. 测试只能靠 monkeypatch 全局状态。

修复后：

```python
# bootstrap.py
def build_runtime(settings=None) -> Runtime:
    store = build_store(settings)
    cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds, max_entries=settings.cache_max_entries)
    limiter = TokenBucketLimiter(rate_per_minute=..., burst=...)
    locks = ResourceLockManager(default_timeout_seconds=...)
    tools = build_default_tool_registry(cache=cache, limiter=limiter, locks=locks,
                                        badcases=badcases, metrics=metrics, store=store)
    llm = build_llm(settings)
    return Runtime(settings=settings, llm=llm, prompts=prompts, ontology=ontology,
                   skills=skills, router=router, tools=tools, agents=agents,
                   store=store, cache=cache, limiter=limiter, locks=locks,
                   metrics=metrics, badcases=badcases, memory=memory)
```

`Runtime` 的所有字段都是**可替换的实现**，不是具体类。把内存存储换成 PostgreSQL，只需改 `build_store` 一处。

```python
# 装配 → 使用
runtime = build_runtime()
service = build_workflow_service(runtime)   # 服务的图与 Runtime 共享同一批 LLM/本体/工具对象
app = FastAPI(...)                          # api/app.py
```

---

## 六、模块职责速查

| 模块 | 一句话职责 | 不该做什么 |
|---|---|---|
| `api/` | 协议转换、鉴权、限流 | 不做业务判断、不直接调工具 |
| `cli.py` | 运维入口 | 不实现业务逻辑 |
| `temporal/` | 持久化编排、审批、状态机 | 不做推理、不直接调 LLM |
| `graph/` | 推理链与本体校验 | 不持有跨重启状态 |
| `agents/` | 单次推理 / 取证 / 规划 | 不决定是否需要审批（那是工具风险的函数） |
| `skills/` `router/` | 意图 → 执行契约 | 不执行任何动作 |
| `tools/` | 受治理的工具执行 | 不做策略判定（委托 governance） |
| `governance/` | 策略与 RBAC 判定 | 不执行工具、不依赖执行细节 |
| `ontology/` | 只读 TBox 与领域词表 | 不存运行实例（ABox） |
| `memory/` | 记忆记录 | 不作为编排状态源 |
| `llm/` | 模型调用与提示词资产 | 不含业务规则 |
| `persistence/` | JSON 文档存储 | 不含业务语义 |
| `resilience/` `concurrency/` | 限流 / 缓存 / 锁 | 无业务知识 |
| `observability/` | Trace / Metrics / BadCase | 不反向依赖任何领域层 |
| `protocol/` | 对外能力发现 | 不含执行逻辑 |
| `bootstrap.py` | 装配 | 不含业务逻辑 |

---

## 七、扩展指南

### 新增一个受治理动作

只需改**两处**——这正是把动作风险声明化的收益：

1. `tools/catalog.py`：在 `TOOL_SPECS` 加一条 `ToolSpec` + handler，在 `ACTION_TO_TOOL` 加映射。
2. `ontology/domain.py`：把动作名加进 `REMEDIATION_ACTIONS`。

审批门、权限校验、幂等、资源锁、BadCase 归档会**自动生效**，无需改任何 Agent 或工作流代码。

### 新增一个 Agent

1. `agents/implementations.py`：继承 `BaseAgent`，声明 `spec`，实现 `execute`。
2. 在 `build_agents()` 注册。
3. `skills/catalog.py`：加对应 `CapabilitySpec` + `SkillSpec`。

`test_every_skill_capability_has_an_agent` 会检查孤立组件。

### 换存储后端

实现 `persistence.base.DocumentStore` 协议的 7 个方法，在 `persistence/factory.py` 注册即可。

### 换 LLM provider

在 `llm/factory.py::build_llm` 加分支，或直接设置 `AIO_AGENTOS_LLM_PROVIDER`。
Agent 代码零改动——它们只依赖 `LLMClient` 协议。
