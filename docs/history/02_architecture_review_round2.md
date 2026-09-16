# AIO-AgentOS v3.5 架构审查报告（第二轮）

- **审查对象**：`aio_agentos_v3_5_knowledge_complete`
- **审查日期**：2026-09-16
- **上一轮报告**：`docs/01_architecture_review.md`
- **审查方式**：全量静态审查（83 个文件全部通读）+ 交叉引用分析 + 运行验证
- **变更规模**：21 文件 / 150 行 Python → **83 文件 / 1668 行 Python**

---

## 一、变更总览

上一轮报告提出 W1–W13 与 Phase 0–5。本轮更新是一次**实质性重写**，不是文档修饰。

| 维度 | 上一轮 | 本轮 | 评价 |
|---|---|---|---|
| 文件总数 | 21 | 83 | 4.0× |
| Python 行数 | 150（全为空壳） | 1668 | 11× |
| 模块数 | 7 | 15 | 补齐 Router / Skills / Tools / Agents / API / Governance / Tests |
| 可执行逻辑 | 0 | 端到端纵切可跑 | 质变 |
| 版本控制 | 无 | git（1 个提交） | ✅ |
| 依赖管理 | 无 | `pyproject.toml` + extras | ✅ |
| 测试 | 0 | 9 个 | ✅ |
| CI / Docker | 无 | GitHub Actions + Dockerfile + compose | ✅ |
| 分层约束 | 无 | import-linter 契约 | ✅ |
| 运行时依赖（pydantic 等） | 无 | 真实引入 | ✅ |

**上一轮 6 条核心建议的采纳情况**：

| 建议 | 采纳 |
|---|---|
| Temporal 外壳 + LangGraph 内核双层拓扑 | ✅ 已落地（`docs/architecture.md`、`knowledge-flow.html`、`temporal/workflow.py`） |
| 新增 `docs/state-ownership.md` | ✅ 已落地，且代码实际遵守 |
| 契约先行：名词升级为可执行 Schema | ✅ 已落地（Pydantic 全覆盖） |
| `protocol/` 拆为出站 MCP / 入站 A2A | ✅ 已拆为 `protocol/mcp/` 与 `protocol/a2a/` |
| 用 import-linter 固化分层 | ⚠️ 部分（仅 3 条规则，见 N15） |
| 优先打通纵向切片 | ✅ 已打通（`InMemoryIncidentWorkflowService`） |

**总评：这一轮的工程质量与上一轮不在同一量级。** 代码是干净、类型完整、有测试的，README 声明的架构原则在参考路径上**基本真实成立**，而不是文档修辞。以下审查以"是否真正接入运行时"为严格标准，因此问题看起来会偏多——这是标准提高的结果，不是质量下降。

---

## 二、W1–W13 落地核验

我逐条核对了 `IMPLEMENTATION_REPORT.md` 的自述，并到代码里验证。

| 项 | 自述 | 核验结果 |
|---|---|---|
| W1 Temporal 拓扑 | 改为外壳+内核 | ✅ **真实**。`temporal/workflow.py` 确实实现了 RUNNING → WAITING_APPROVAL → approve → COMPLETED 的挂起恢复语义 |
| W2 状态所有权 | `WorkflowState` 为事实源 | ✅ **真实**。`graph/pipeline.py` 为每个 Agent 新建 `AgentContext`（不跨调用复用），编排字段只在 `WorkflowState`，`state-ownership.md` 的规则在代码里被遵守 |
| W3 文档/实现失衡 | 名词升级为 Schema | ⚠️ **大部分真实**，但 `AgentSpec.input_schema` 6 个 Agent 全是 `{"type":"object"}` 占位，无校验能力（N19） |
| W4 工程基线 | pyproject/CI/Docker/测试 | ⚠️ **文件齐备，但 CI 未跑通**（N22） |
| W5 横切层 | 改为共享层/拦截器 | ✅ **真实**。Governance 确实在 `ToolRegistry.invoke` 内部前置执行；Trace 用 `finally` 保证失败路径落盘 |
| W6 契约缺失 | API/Agent/Skill/Tool/Card/MCP/Trace | ✅ **真实**，Schema 齐备 |
| W7 Tool/Router 缺失 | 新增 `tools/` `skills/` `router/` | ⚠️ **建了模块，未接通链路**（N1、N4） |
| W8 失败/幂等 | 幂等 + 超时 + 重试 + 熔断 | ⚠️ **部分真实**。API 幂等 ✅、审批超时 ✅、熔断 ✅；但 Tool 层重试无幂等键（N8）、幂等表为进程内（N9） |
| W9 Evaluation | EvalResult + fixture | ⚠️ **结构真实，数据无意义**。fixture 仅 3 条，与代码 if/elif 分支一一同构（N12） |
| W10 安全闭环 | 四级风险 + RBAC + 拦截器 + 默认拒绝 | ⚠️ **策略真实，闭环有缺口**（N6、N7） |
| W11 版本演进 | OntologyVersion + semver | ⚠️ **真实但未强制**。`compatible_from` 只存储、从不校验兼容性 |
| W12 领域命名 | README 明确 AIOps | ✅ 已澄清（父目录未改，已说明理由） |
| W13 V3 基线 | 改为可执行基线 | ✅ **真实**，不再伪造不存在的 V3 |

**结论：13 项中 6 项完全落地、7 项部分落地、0 项虚假陈述。** 自述的可信度显著高于上一轮。

### 2.1 运行验证结果（实测）

我在隔离环境中实际安装了项目并运行了全部验证命令：

| 命令 | 结果 | 与自述是否一致 |
|---|---|---|
| `pytest -q` | **9 passed in 5.76s** | ✅ 完全一致 |
| `python -m examples.demo` | 完整跑通 WAITING_APPROVAL → 审批 → governed tool → COMPLETED，产出 5 个 Span | ✅ 完全一致 |
| `lint-imports` | **3 kept, 0 broken**（53 files / 80 dependencies） | ✅ 完全一致 |
| `ruff check .` | **41 errors，退出码 1** | ❌ **CI 会失败**（见 N22） |

`ruff` 错误分布：`I001` 导入排序 ×19、`UP017` 使用 `datetime.UTC` ×10、`UP042` `str+Enum` 应改 `StrEnum` ×6、`B904` `raise ... from err` ×5、`UP035` ×1。

**因此 `IMPLEMENTATION_REPORT.md` 的"已验证"三项（pytest / compileall / demo）属实**，但它没有提到 CI 的第一道关卡 `ruff check .` 是红的。

### 2.2 行为级验证（实测复现）

除静态审查外，我编写脚本实测复现了 4 个关键问题（脚本已清理，未留在仓库）：

```text
=== N6: approver 角色能否执行 HIGH 风险工具？ ===
  approver 权限集: ['approval:decide', 'tool:medium', 'tool:read']
  HIGH + approved=True  -> True | approved high-risk action        ← 越权确认
  HIGH + approved=False -> False | requires_approval = True

=== N8: 重试是否重复执行非幂等副作用？ ===
  handler 实际被调用次数: 3 (配置 max_retries=2)                   ← 副作用重复执行

=== N21: Span 是否有父子层级？ ===
  span=inner  parent_span_id=None
  span=outer  parent_span_id=None                                  ← 层级丢失

=== N5: 审批门来源（Agent 启发式 vs Tool 风险等级） ===
  root_cause=upstream_timeout             -> action=restart_service        requires_approval=True
  root_cause=database_dependency_failure  -> action=collect_more_evidence  requires_approval=False
```

四项均确认成立，详见 §7 的 N5 / N6 / N8 / N21。

---

## 三、模块职责划分（现状）

15 个模块，职责边界比上一轮清晰得多：

| 层 | 模块 | 职责 | 运行时可达 |
|---|---|---|---|
| L4 | `api/` | FastAPI 4 端点 + 鉴权 + 幂等 | ✅ |
| L4 | `temporal/` | 外层持久化编排、审批信号、超时 | ✅（参考实现） |
| L4 | `graph/` | 内层推理图 | ✅（pipeline） |
| L3 | `governance/` | 风险分级、RBAC、Policy 拦截器 | ✅ |
| L3 | `protocol/` | MCP 出站目录 / A2A AgentCard | ❌ 纯 Schema |
| L2 | `router/` `skills/` | Intent→Skill→Capability→Agent→Tool→Workflow | ❌ **未接线** |
| L2 | `tools/` | ToolSpec + Registry + Policy 前置 + 超时/重试/熔断 | ✅ |
| L2 | `agents/` | 6 个 Agent 规范 + 参考实现 | ⚠️ 3/6 可达 |
| L1 | `ontology/` | 只读 TBox + Axiom + 版本 | ❌ **未接线** |
| L1 | `memory/` | 4 类记录 + store | ✅ |
| L0 | `observability/` | Span / Metric / Eval / Feedback | ⚠️ 仅 Span 有生产者 |
| L1 | `runtime/` | AgentContext + 生命周期状态机 | ✅ |

### 3.1 职责划分的优点

1. **`runtime/lifecycle.py` 用显式状态转移表**（`_ALLOWED`），非法转换直接抛错，而不是静默接受。这是很扎实的做法。
2. **`observability/trace.py` 用 `@contextmanager` + `finally`**，异常时先标记 `SpanStatus.ERROR` 再落盘再 `raise`。这正好兑现了 `observability-design.md` 里"异常会在 span finally 路径落盘"的承诺。
3. **`tools/registry.py` 把 Policy 检查放在重试循环之外**——策略拒绝不会被重试掩盖。正确的顺序。
4. **`agents/base.py` 用 try/except 保证生命周期落到 FAILED**，不会卡在 RUNNING。
5. **`protocol/` 按方向拆分**，出站（MCP）/入站（A2A）不再混淆。

### 3.2 职责划分的缺口

**缺口一：`graph/` 内部有两套并行实现（N2）**

- `graph/pipeline.py` → `IncidentReasoningGraph`，运行时使用
- `graph/langgraph_adapter.py` → `build_state_graph()`，**仅测试使用**

两套实现重复了同一段业务启发式：

```python
# agents/implementations.py  ReasoningAgent.execute
if "database" in messages or "db" in messages: cause = "database_dependency_failure"
elif "timeout" in messages:                   cause = "upstream_timeout"
elif "memory" in messages or "oom" in messages: cause = "resource_exhaustion"

# graph/langgraph_adapter.py  reason()
if "database" in messages or "db" in messages: cause, confidence = "database_dependency_failure", 0.8
elif "timeout" in messages:                    cause, confidence = "upstream_timeout", 0.8
elif "memory" in messages or "oom" in messages: cause, confidence = "resource_exhaustion", 0.8
```

README 声称"LangGraph/Reasoning Graph 是内层短时推理图"，但**运行时走的是 `pipeline.py`，LangGraph 从未被调用**。这意味着：装不装 `[orchestration]` extra，运行时行为完全一样。这是当前最容易被误判为"已完成"的地方。

**缺口二：`router/` + `skills/` 是完整的库，但没有任何调用点（N1）**

`RouteSkillRouter`、`SkillRegistry`、`SkillSpec`、`CapabilitySpec` 在 `tests/` 之外**零引用**。运行时路径是：

```python
# graph/pipeline.py
self.log_agent = LogAgent()
self.reasoning_agent = ReasoningAgent()
self.remediation_agent = RemediationAgent()
```

**硬编码。** 于是 `docs/skill-design.md` 与 README 反复强调的"Intent → Skill → Capability → Agent → Tool → Workflow"五级路由，在真实执行中**完全不存在**——它是被测试覆盖的孤立组件。W7 的真实状态是"建了模块"，不是"接通了链路"。

**缺口三：3 个 Agent 不可达（N11）**

`AlarmAgent`、`TopologyAgent`、`TicketAgent` 在 `AGENTS` 字典里注册，但 **`AGENTS` 字典本身在运行时零引用**。只有 log / reasoning / remediation 三个被 `pipeline.py` 直接实例化。`agent-design.md` 说 Alarm/Topology/Log 是 Evidence Agents，但 Alarm 与 Topology 从未参与任何流程。

**缺口四：`ontology/` 是完整的库，但运行时零引用（N10）**

`OntologyRegistry` 只在 `ontology/` 内部与测试中出现。关键证据：

```python
# graph/langgraph_adapter.py
async def supervise(state):
    return supervisor_node(dict(state))   # ← 没有传 ontology
```

`supervisor_node(state, ontology=None)` 的 ontology 分支**永远走不到**。因此：
- "Supervisor 校验 Ontology 公理"这一设计**从未生效**
- `AxiomKind.HIGH_RISK_REQUIRES_APPROVAL` 这条公理与 `PolicyEngine` 逻辑重复，且完全死代码

---

## 四、模块依赖关系

### 4.1 事实层面：依赖关系已建立且方向合理

上一轮"模块间零 import"的问题已解决。当前实际依赖图（箭头 = 依赖）：

```
api/ ──────────────► temporal/ ──────► graph/ ──────► agents/ ──► runtime/
 │                        │                              │          │
 │                        ├──► tools/ ──► governance/ ◄──┘          │
 │                        ├──► memory/                              │
 └──► tools/              └──► observability/ ◄─────────────────────┘
```

观察到的实际依赖：
- `tools/registry.py` → `governance`、`observability`（正确的横切依赖）
- `temporal/workflow.py` → `graph`、`memory`、`tools`、`governance`（外层编排聚合，合理）
- `graph/pipeline.py` → `agents`、`runtime`、`observability`（合理）
- `agents/base.py` → `runtime`、`observability`（合理）
- **`ontology/` 与 `protocol/` 没有任何入边**（因为无人使用）

**没有发现反向依赖或循环依赖。** 这是本轮最扎实的成果之一。

### 4.2 契约层面：只固化了 3 条规则（N15）

`pyproject.toml` 的 import-linter 配置只有 3 条：

1. `observability` 是叶子依赖 ✅
2. `ontology` / `memory` 不依赖 `graph` / `temporal` / `api` ✅
3. `ontology` / `memory` 不依赖 `protocol` ✅

但 `docs/architecture.md` 声明的 L0–L4 分层**未被完整编码**。缺失的关键约束：

- **L2（`skills` / `tools` / `router` / `agents`）不得依赖 L4（`graph` / `temporal` / `api`）**——目前 `graph/` 可以随意 import `agents/`，但反向也应禁止，否则会形成环
- **`governance` 不应依赖 `tools`**（当前 `tools` → `governance` 单向，但无约束防止反向）
- **`protocol` 不应依赖 `graph` / `temporal`**

建议补齐为 6–8 条契约，让 `docs/architecture.md` 的分层声明与 CI 检查一一对应。

### 4.3 遗留的兼容层（低风险）

`ontology/axiom.py`、`ontology/relation.py`、`protocol/agent_card.py`、`protocol/mcp_server.py` 是纯转发 shim。`temporal/temporalio_adapter.py` 更是只有一个 `temporalio_available()` 探针、**没有任何适配器代码**——是空模块。这些是上一轮"知识骨架"的残留，建议清理或补实。

---

## 五、数据流向

### 5.1 控制流拓扑：已修正 ✅

上一轮的致命问题（Temporal 被画成 Governance 的下游）**已完全修复**。现在三处表达一致：

- `docs/architecture.md`：Temporal 外壳包含 LangGraph Activity，Governance 在 Tool 调用前
- `docs/knowledge-flow.html`：同样的嵌套结构
- `temporal/workflow.py`：代码层面真实实现了挂起/恢复

参考路径的实际数据流（已验证可跑通）：

```
POST /alarm
  → _idempotency 查重
  → InMemoryIncidentWorkflowService.start()  → WorkflowState(PENDING)
  → asyncio.create_task(_run)  → status=RUNNING
  → trace.span("workflow.reasoning")
      → IncidentReasoningGraph.run()
          → LogAgent      (新建 AgentContext)
          → ReasoningAgent(新建 AgentContext)
          → RemediationAgent(新建 AgentContext)
  → proposed_action="restart_service" → approval_required=True
  → status=WAITING_APPROVAL + 启动超时任务
  ── 人工审批 ──
POST /approval/{id}
  → service.approve() → 校验 approval:decide → 取消超时任务
  → 切换为 workflow-executor(admin) 身份
  → ToolRegistry.invoke("service.restart", approved=True)
      → PolicyEngine.evaluate_tool(HIGH, approved=True)
      → trace.span("tool.invoke")
      → _restart_service()
  → status=COMPLETED + 写 EpisodicMemory("completed")
```

这条链路是**真实的端到端纵切**，不是文档。上一轮 Phase 3 的核心建议已被采纳并实现。

### 5.2 但存在两条语义分叉的工作流实现（N3，本轮最重要发现）

| 维度 | `temporal/workflow.py`（参考，测试/API 使用） | `temporal/production.py`（真实 Temporal） |
|---|---|---|
| 审批超时 | 置 `TIMED_OUT` + 写 Episodic | 返回 `status="timed_out"`，**不写 Memory** |
| 审批拒绝 | 置 `REJECTED` + 写 Episodic | 返回 `status="rejected"`，**不写 Memory** |
| 完成 | 写 Episodic("completed") | **不写 Memory** |
| 修复执行 | `ToolRegistry.invoke` → Policy 拦截 | **硬编码 stub，完全绕过 ToolRegistry / PolicyEngine** |
| 测试覆盖 | 3 个测试 | **0 个测试** |

`temporal/production.py` 的修复活动：

```python
@activity.defn
async def remediation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    # Replace with a governed MCP/infra adapter. Kept side-effect-free in this template.
    return {"action": payload["action"], "service": payload.get("service","unknown"),
            "executed": True, "mode": "temporal-demo"}
```

README 已诚实标注"生产环境应替换为经过 MCP/基础设施适配器的真实 Activity"。但架构层面的问题是：**README 的第一条原则"Governance 是 Tool interceptor：每次 Tool 调用都执行 Policy/RBAC/Risk 检查"只在参考路径成立，在生产路径不成立。**

同时 `docs/security-design.md` 明确写"拒绝/超时都会写 Episodic Memory"，而生产路径两者都不写——**文档与生产实现直接冲突**。

**建议**：把 `remediation_activity` 与 episode 写入抽成两条实现共享的抽象（例如一个 `IncidentOrchestrator` 协议 + 两个 backend），或至少在 `production.py` 中调用同一个 `ToolRegistry`/`MemoryStore` 接口。否则两条路径必然持续漂移。

### 5.3 状态所有权：已建立且被遵守 ✅

`docs/state-ownership.md` 是本轮质量最高的文档。核心规则在代码中得到遵守：

- 编排字段只在 `WorkflowState`（`root_cause` / `confidence` / `proposed_action` / `approved`）
- `AgentContext` 每次 Agent 调用新建，不跨调用复用（`pipeline.py` 中三处独立构造）
- Memory 是派生数据，不参与编排状态重建

**唯一未闭合的**：文档说"Incident episode 由 Workflow completion handler 写入"，但生产路径没有 completion handler（见 §5.2）。

---

## 六、核心设计决策评估

| 决策 | 评价 | 说明 |
|---|---|---|
| Temporal 外壳 + Graph 内核双层拓扑 | ✅ **正确，已落地** | 上一轮致命问题已修复，且代码真实实现挂起恢复 |
| `WorkflowState` 为编排字段唯一事实源 | ✅ **正确，已落地** | 配合"AgentContext 不跨调用复用"，从结构上消除了状态漂移 |
| Governance 作为 Tool interceptor | ✅ **方向正确，但仅覆盖一条路径** | 参考路径真实前置；生产路径绕过（N3） |
| 四级风险枚举 + RBAC 权限表 | ✅ **正确** | `RiskLevel` 用 `IntEnum`，可直接比较与排序 |
| Trace 用 contextmanager + finally | ✅ **优秀** | 失败路径不留白，是同类项目常见疏漏点 |
| Policy 检查在重试循环之外 | ✅ **优秀** | 避免策略拒绝被重试掩盖 |
| 审批超时默认拒绝 | ✅ **正确** | 且提供"可升级审批人但不自动放行"的扩展说明 |
| 生命周期显式状态转移表 | ✅ **正确** | 非法转换抛错而非静默接受 |
| Router 五级路由 | ⚠️ **设计合理，实现孤立** | 组件完整但未接线（N1） |
| Ontology 驱动 Supervisor 校验 | ⚠️ **设计合理，实现孤立** | ontology 参数永远为 None（N10） |
| LangGraph 作为生产内层图 | ⚠️ **声明与实现不符** | 运行时用 `pipeline.py`，adapter 仅测试可达（N2） |
| Agent 推导 `requires_approval` | ⚠️ **耦合错误** | 审批门应由 `ToolSpec.risk_level` 决定（N5） |
| 审批权 = 执行权 | ❌ **削弱职责分离** | `approval:decide` 可执行高风险（N6） |

---

## 七、本轮新发现问题（按严重度）

### 🔴 高

**N1｜Router/Skills 未接入执行路径**
`RouteSkillRouter` / `SkillRegistry` / `SkillSpec` 在运行时零引用。架构文档的核心路由链路在真实执行中不存在，Agent 由 `pipeline.py` 硬编码实例化。→ 要么接线，要么把文档降级为"规划中"。

**N2｜`graph/` 双实现，业务逻辑重复**
`pipeline.py`（运行时）与 `langgraph_adapter.py`（仅测试）各自实现同一套根因启发式。改动一处不会同步另一处。→ 保留一条实现，另一条改为薄适配层。

**N3｜生产工作流绕过治理且不写 Memory**
`temporal/production.py` 的 `remediation_activity` 硬编码返回，不经 `ToolRegistry`/`PolicyEngine`；且拒绝/超时/完成均不写 Episodic。与 `security-design.md` 明文冲突。→ 抽取共享编排接口。

**N4｜`AgentSpec.tool_permissions` 与 `SkillSpec.allowed_tools` 从未强制执行**
声明了白名单，但没有任何代码检查 Agent 是否只调用了被允许的 Tool。更根本的是：**Agent 从不调用 `ToolRegistry`**——`LogAgent` 只是把 `context.inputs["logs"]` 格式化，并不调用 `logs.search` 工具。整条"Skill 白名单 → Agent 权限 → Tool 执行"链路只有最后一环（RBAC 权限字符串）是真的。

### 🟠 中高

**N5｜审批门由 Agent 启发式推导，而非 Tool 风险等级**
```python
# agents/implementations.py
return {"action": action, "requires_approval": action == "restart_service"}
```
是否要人工审批，取决于 Agent 对动作字符串的判断，而不是 `ToolSpec.risk_level`。风险等级在系统里有**两个独立来源**（Agent 启发式 + PolicyEngine），必然漂移。
（注：当前是 **fail-safe**——若 Agent 误判为无需审批而工具实为 HIGH，Policy 会拒绝导致工作流 FAILED，不会越权执行。但失败方式很差：应进入 WAITING_APPROVAL 而非 FAILED。）
→ 应由 `ToolRegistry` 查询 `ToolSpec.risk_level` 决定是否需要审批。

**N6｜`approval:decide` 被当作可执行高风险的权限**
```python
if "tool:high" not in perms and "approval:decide" not in perms:
    return PolicyDecision(allowed=False, reason="principal lacks high-risk permission")
```
按 `security-design.md` 的 RBAC 表，`approver` 只有 `tool:read` / `tool:medium` / `approval:decide`，**不含 `tool:high`**。但这段逻辑让 approver 在审批通过后可以自己执行高风险工具——**把"审批权"和"执行权"混为一谈**。

**实测已复现**：approver 的权限集为 `['approval:decide','tool:medium','tool:read']`，对 HIGH 风险工具传 `approved=True` 时 `allowed=True`。

对比 `temporal/workflow.py` 中的注释，作者的意图恰恰相反：
```python
# Separation of duties: the human principal approves; a governed workflow service
# identity executes the already-approved action.
executor = Principal(subject="workflow-executor", roles={"admin"})
```
**引擎逻辑与设计意图矛盾。** → 移除 `or "approval:decide"` 条件，或引入显式 `tool:execute_high` 权限。

**N7｜默认零鉴权 + API 硬编码审批角色**
- `_check_api_key` 仅在设置了 `AIO_AGENTOS_API_KEY` 时生效；`_idempotency` 同样。未设置时**所有端点完全开放**。
- `docker-compose.yml` 传的是 `AIO_AGENTOS_API_KEY: ${AIO_AGENTOS_API_KEY:-}`（空默认值），**且完全没有传 `AIO_AGENTOS_APPROVER_KEY`**。即：按仓库提供的 compose 启动，任何能访问 8000 端口的人都可以批准高风险服务重启。
- `api/app.py` 对所有审批请求硬编码 `roles={"approver"}`，且 `subject=req.approver` 是请求体自报的字符串。→ 无法区分"谁在审批"，**也不检查审批人是否就是提出者**（无禁止自审）。

文档已说明"生产环境应替换为 OIDC/mTLS"，但默认配置的开放性仍应显式收紧（例如未配置密钥时拒绝启动，而不是放行）。

### 🟡 中

**N8｜Tool 重试无幂等键，且重试非幂等副作用**
`ToolRegistry.invoke` 对**所有**异常重试（含不可重试的类型错误/校验错误），且重试的是真实副作用操作。`service.restart` 配置 `max_retries=1` 意味着**可能重启两次**。Tool 层没有 `idempotency_key` 参数。

**实测已复现**：`max_retries=2` 时 handler 实际被调用 **3 次**（1 次初始 + 2 次重试），全部重复执行。

→ 重试前应判定异常可重试性，并为副作用型 Tool 引入幂等键。

**N9｜幂等表与熔断器是进程内状态**
`api/app.py` 的 `_idempotency` 是模块级 dict，`ToolRegistry` 的 `circuit_open_until` 在实例内。在文档描述的 K8s 多 Pod 部署下：熔断器各 Pod 独立（形同虚设）；幂等表**重启即丢失**——而 Temporal 的核心价值正是跨重启持久化，两者语义冲突。同时 `_idempotency` 无上限增长（内存泄漏）。

**N10｜Ontology 未接线，Supervisor 校验为死代码**
见 §3.2 缺口四。`AxiomKind.HIGH_RISK_REQUIRES_APPROVAL` 与 PolicyEngine 逻辑重复且永不执行。

**N11｜3/6 Agent 孤立；MCP/A2A 无实例**
`AlarmAgent` / `TopologyAgent` / `TicketAgent` 不可达；`AGENTS` 字典零引用。`AgentCard` 与 `MCPToolCatalog` 均无运行时实例——A2A 无对外能力声明，MCP 无发布工具。

**N12｜Evaluation 无生产者，数据集同义反复**
`EvalResult` / `MetricSample` / `Feedback` 无任何运行时生产者（只有 Span 被写入）。`synthetic_cases.json` 仅 3 条，且分别对应代码里 3 个 if/elif 分支——**测试与被测逻辑同构，几乎没有区分度**。`docs/evaluation.md` 要求的 ≥30 条真实 case 尚未接入。

**N21｜Trace 是扁平的，没有父子层级（实测确认）**
`Span` 定义了 `parent_span_id` 字段，但**所有调用点都不传它**，且 `AgentTrace.span()` 用显式参数而非"当前 Span 栈"来推导父子关系。因此嵌套的 `with trace.span(...)` 会产生**兄弟节点而非子节点**。

实测：
```python
with t.span("outer"):
    with t.span("inner"):
        pass
# 结果：span=inner parent_span_id=None
#       span=outer parent_span_id=None
```
demo 运行的 5 个 Span（`workflow.reasoning` / 3×`agent.run` / `tool.invoke`）**全部 `parent_span_id=None`**，彼此平级。

`observability-design.md` 写明 Span 应描述"节点/Agent/Tool/Workflow"的层次，但扁平 Trace 无法回答"哪个 Agent 里的哪次 Tool 调用超时了"——而这正是 Agent 系统排障最需要的下钻能力。
→ 在 `AgentTrace` 内维护一个 Span 栈（`contextvars`），`span()` 自动从栈顶取 `parent_span_id`。

**N22｜CI 从未真实跑通（实测确认）**
`.github/workflows/ci.yml` 的第二个步骤是 `ruff check .`。实测该命令**退出码为 1，报 41 个错误**：

| 规则 | 数量 | 含义 |
|---|---|---|
| `I001` | 19 | 导入块未排序 |
| `UP017` | 10 | 应使用 `datetime.UTC` |
| `UP042` | 6 | `str + Enum` 应改 `StrEnum`（含 `MemoryType`、`AgentLifecycle`） |
| `B904` | 5 | `except` 中应 `raise ... from err` |
| `UP035` | 1 | 弃用的导入来源 |

CI 会在 `ruff` 这一步中断，**`lint-imports` 与 `pytest` 根本不会执行**。结合仓库只有一条 squash 提交（N17），可以判断 CI 配置是写完后未运行过。

**这直接影响"W4 工程基线已完成"的结论**——基线文件齐备，但质量门禁是红的。修复成本极低（`ruff check --fix` 可自动修复 30 项），但必须真正跑一次。

**N13｜工作流客户端/Worker 概念重叠 4 份**
`temporal/client.py`（本地）、`temporal/sdk_client.py`（真实）、`temporal/worker.py`（转发）、`temporal/sdk_worker.py`（真实）。其中 `client.py` 只是一个转发 facade。命名无法表达"参考实现 vs 生产实现"，建议按 `local/` 与 `production/` 重命名。

### 🟢 低

**N14｜`COMPLETED` 状态语义混淆**
`collect_more_evidence` 路径下 `remediation_result={"action":..., "executed": False}`，工作流仍置 `COMPLETED`。即"什么都没做"与"已修复"共用同一终态。建议区分 `RESOLVED` / `NO_ACTION`。

**N15｜import-linter 仅固化 3 条规则**（见 §4.2）

**N16｜`Role` 类形同虚设**
```python
class Role(str):            # governance/models.py
    VIEWER = "viewer"
    ...
```
继承 `str` 无实际用途，且 `PolicyEngine.ROLE_PERMISSIONS` 与 `api/app.py` 中均使用硬编码字符串 `"approver"` / `"admin"`，从未引用 `Role`。→ 改为 `str, Enum` 并在全部调用点使用。

**N17｜单条 squash 提交**
只有一个提交 `Complete v3.5 architecture baseline`，无演进历史，无法追溯或二分定位。

**N18｜审批路径存在竞态**
`InMemoryIncidentWorkflowService` 持有 `asyncio.Lock`，但 `approve()` / `_approval_timeout()` / `_execute_remediation()` 均未使用它。`approve()` 中 `timeout_task.cancel()` 在 sleep 已完成时无法中断后续无 await 的代码，存在 `TIMED_OUT` 与审批并发写入的可能。

**N19｜`AgentSpec.input_schema` 为占位符**
6 个 Agent 的 `input_schema` 全为 `{"type":"object"}`，`output_schema` 仅有 properties 声明。这些 Schema 从未用于校验任何输入输出，与 README"可执行 Schema"的表述有落差。

**N20｜`ontology` / `protocol` 的 shim 与空模块**
`temporal/temporalio_adapter.py` 只有探针函数、无适配代码；4 个转发 shim 可清理。

---

## 八、进度评估

进度需用**两把尺子**衡量，否则会得出误导性结论：

### 尺子一：作为"可运行的参考实现"

| 模块 | 完成度 | 说明 |
|---|---|---|
| 工程基线 | 95% | git/pyproject/CI/Docker/compose/env 齐备 |
| 契约层 | 90% | Pydantic 全覆盖，部分 Schema 未强制校验 |
| Runtime | 90% | 上下文 + 显式状态机 |
| Memory | 85% | 4 类记录 + 线程安全 store（进程内） |
| Governance | 80% | 风险/RBAC/拦截器完整；职责分离有缺口 |
| Tools | 75% | Registry + Policy + 超时/重试/熔断；幂等缺失 |
| Observability | 60% | Span 真实可用；Metric/Eval/Feedback 无生产者 |
| Agents | 55% | 3/6 接入；权限白名单未强制 |
| Graph | 55% | pipeline 可用；LangGraph adapter 未接线且重复实现 |
| Temporal | 55% | 参考实现完整可跑；生产实现语义分叉且无测试 |
| API | 80% | 4 端点 + 幂等 + 鉴权（默认开放） |
| **Router / Skills** | **30%** | 库完整，**运行时零引用** |
| **Ontology** | **35%** | 库完整，**运行时零引用** |
| **Protocol** | **25%** | 纯 Schema，无实例、无 transport |
| **Evaluation** | **20%** | 结构在，数据集 3 条且同义反复 |

**综合：约 65%**——一条真实可跑的纵切 + 完整的契约层 + 测试与 CI，作为参考实现是合格的。

### 尺子二：作为"生产级系统"

**约 25%。** 未开始的部分：

- 真实 Temporal Server / Namespace / TLS 接入
- PostgreSQL / VectorDB adapter（当前全部为进程内内存）
- 真实 MCP transport 与 Tool 实现（`logs.search` / `topology.lookup` / `ticket.create` 均不存在）
- OIDC / mTLS / 企业 IAM
- Kafka / Alarm 平台 connector
- 真实 golden set（≥30 条脱敏历史故障）
- K8s 部署清单、限流、多租户
- 可观测后端导出（OTel）
- 分布式状态（幂等表、熔断器外置）

`IMPLEMENTATION_REPORT.md` 已诚实列出这些，**没有伪造基础设施或历史数据**——这一点值得肯定。

### 已完成 / 进行中 / 未开始

**已完成（可运行、已接入执行路径、有测试）**
工程基线、契约层、Ontology 库、Memory、Observability(Span)、Runtime、Governance(策略)、Tools(Registry)、Agents(BaseAgent + 3 个)、Graph(pipeline)、Temporal(参考实现)、API(4 端点)、9 个测试

> 例外：工程基线中 `ruff check .` 为红（N22），CI 实际不可用。

**进行中（结构存在但未接入或未闭环）**
Router/Skills（未接线）、Ontology（未接线）、LangGraph adapter（未接线 + 重复实现）、Temporal production（语义分叉 + 无测试）、Protocol MCP/A2A（无实例）、Agents（3/6 孤立）、Observability（仅 Span）、Evaluation（无生产者）、安全（职责分离 + 默认鉴权）

**未开始**
真实基础设施接入、真实 Tool 实现、Kafka/Alarm connector、真实 golden set、K8s/限流/多租户、OTel 导出、分布式状态

---

## 九、改进建议

### Phase A — 消除"孤立组件"（约 3–5 天，最高优先级）

当前最大的架构风险不是缺功能，而是**文档声明的链路与真实执行的链路不一致**。这会持续误导后续开发者。

1. **先修 CI（N22）**：`ruff check --fix` 修复 30 项，手工处理 5 个 `B904` 与 1 个 `UP035`，确保 `ruff` / `lint-imports` / `pytest` 三道关卡真正跑绿。这是后续所有工作的前提——没有绿色门禁，Phase A 的接线改动无法验证
2. **接线 Router（N1）**：把 `IncidentReasoningGraph` 的硬编码 Agent 实例化改为经 `RouteSkillRouter` 路由；为 alarm / log / topology / reasoning / remediation / ticket 注册对应 Skill
3. **接线 Ontology（N10）**：在 `supervisor_node` 调用处传入真实 `OntologyRegistry`；在 `pipeline.py` 中于图末端调用 supervisor，并把 errors 写入 `WorkflowState`
4. **合并 Graph 双实现（N2）**：保留 `pipeline.py` 为唯一逻辑源，`langgraph_adapter.py` 改为调用 `AGENTS` 中的 Agent 实例（或反之），确保只有一份根因启发式
5. **补齐 import-linter 至 6–8 条**（N15），与 `docs/architecture.md` 的 L0–L4 一一对应
6. **修复 Span 层级（N21）**：在 `AgentTrace` 中用 `contextvars` 维护 Span 栈，使嵌套 `span()` 自动建立父子关系

**验收**：`RouteSkillRouter` 与 `OntologyRegistry` 出现在 `grep` 的非测试调用点；删除 `langgraph_adapter.py` 中的启发式后测试仍全绿；demo 的 Span 呈现树形层级；`ruff` 退出码为 0。

### Phase B — 收紧治理语义（约 2–3 天）

5. **审批门改由 Tool 风险驱动（N5）**：`WorkflowState.approval_required` 从 `ToolSpec.risk_level >= HIGH` 推导，而非 Agent 字符串判断
6. **修正职责分离（N6）**：移除 `evaluate_tool` 中的 `or "approval:decide"`；明确"审批"与"执行"是两种权限
7. **禁止自审**：`approve()` 增加 `principal.subject != state.proposed_by` 校验
8. **默认拒绝启动（N7）**：未配置 `AIO_AGENTOS_API_KEY` 时拒绝启动（而非放行）；compose 补传 approver key
9. **强制 Tool 白名单（N4）**：在 `BaseAgent` 中校验 `AgentSpec.tool_permissions` 与 `SkillSpec.allowed_tools` 的交集；`ToolRegistry.invoke` 增加 `caller_agent` 参数并校验

**验收**：新增测试覆盖"approver 无法执行高风险"、"自审被拒"、"未配置密钥时服务拒绝启动"。

### Phase C — 生产路径对齐（约 1 周）

10. **抽取共享编排抽象（N3）**：定义 `IncidentOrchestrator` 协议（reason / propose / execute / record_episode），`InMemoryIncidentWorkflowService` 与 `IncidentWorkflow` 各自实现，但共享 Tool 治理与 Memory 写入逻辑
11. **生产路径补 Memory 与治理**：`remediation_activity` 改为经 `ToolRegistry.invoke`；拒绝/超时/完成均写 Episodic
12. **生产路径补测试**：用 Temporal 测试环境或 mock 覆盖 WAITING_APPROVAL / 拒绝 / 超时三条路径
13. **重命名消除歧义（N13）**：`temporal/local/` 与 `temporal/production/`

### Phase D — 状态与可靠性（约 1 周）

14. **幂等与熔断外置（N9）**：`_idempotency` 移入 `WorkflowState` 或外部存储；熔断器状态外置到 Redis/共享层
15. **Tool 重试语义（N8）**：区分可重试/不可重试异常；副作用型 Tool 强制要求 `idempotency_key`
16. **状态语义细化（N14）**：区分 `RESOLVED` / `NO_ACTION` / `FAILED`
17. **消除审批竞态（N18）**：`approve()` 与 `_approval_timeout()` 加锁

### Phase E — 真实接入（按需）

18. 真实 Tool 实现 + MCP transport
19. PostgreSQL / VectorDB adapter（实现 `MemoryStore` 接口）
20. OIDC/mTLS 替换 API key
21. ≥30 条真实 golden set + CI 回归门禁（N12）
22. OTel 导出、K8s 清单、限流

### 三个"不要做"

1. **不要在 Phase A 完成前增加新模块**——已有 4 个孤立组件，再加只会扩大"文档描述的系统"与"运行的系统"的差距
2. **不要让 `production.py` 继续独立演化**——它与参考实现已经在语义上分叉（N3）
3. **不要把 3 条 synthetic case 当作评测**——它与代码分支同构，无法发现任何回归

---

## 十、结论

**这一轮是实质性跃升。** 上一轮报告的核心问题——控制流拓扑错误、状态所有权缺失、文档与实现失衡、工程基线为零——**全部得到真实修复**，而且修复方式是对的：`docs/state-ownership.md` 的规则在代码里被遵守，Governance 真的变成了拦截器，Trace 真的在失败路径落盘，Temporal 真的实现了挂起恢复。6 条核心建议有 5 条完整采纳。代码干净、类型完整、有测试与 CI，`IMPLEMENTATION_REPORT.md` 的自述经核验无虚假陈述。

**当前最需要正视的问题是"孤立组件"而非"缺失功能"。** 四个组件（Router/Skills、Ontology、LangGraph adapter、Protocol）在代码库里完整存在、被测试覆盖、被文档重点描述，**但在真实执行路径中零引用**。这会造成一种比"没做"更危险的状态：看文档以为架构已闭环，看代码以为模块已就位，只有追执行路径才会发现它们从未运行。同理，`temporal/production.py` 与参考实现已经语义分叉，且无测试覆盖。

**第二个需要正视的是治理语义的两处不一致**：审批门由 Agent 启发式而非 Tool 风险等级驱动（N5），以及 `approval:decide` 被当作高风险执行权限（N6）——后者与代码里自己写的 "separation of duties" 注释直接矛盾。这两点已通过实测复现。

**第三个是质量门禁本身是红的**：`ruff check .` 退出码 1（41 个错误），CI 会在第一步之后中断。修复成本很低，但"基线已完成"的结论在此之前不成立。

**建议的下一步**：Phase A（修 CI + 消除孤立组件）+ Phase B（收紧治理语义），合计约一周，收益远高于继续增加模块。判据很明确——`ruff` 退出码为 0、`RouteSkillRouter` 与 `OntologyRegistry` 出现在非测试的调用点上、demo 的 Trace 呈现树形层级。
