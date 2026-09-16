# AIO-AgentOS v3.5

> **一个面向 AIOps 故障根因分析与受治理修复的多 Agent 运行时参考实现。**
> 本体驱动的语义约束、双层编排（持久化外壳 + 短时推理图）、工具级治理拦截、
> 全链路可观测，以及可运行的失败闭环。

这不是架构示意图的堆砌：**仓库里的每一条能力都有对应的可执行代码和测试**。
`aio-agentos check` 会把所有主要模块真的跑一遍，而不是只 import。

---

## 30 秒跑起来

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

aio-agentos check     # 自检：配置 / 本体 / 路由 / 工具 / 端到端链路
aio-agentos demo      # 跑通「告警 → 推理 → 审批 → 受治理修复」
aio-agentos serve     # 启动 API（http://127.0.0.1:8000/docs）
```

不需要 API Key、不需要 Temporal Server、不需要外部数据库——
默认使用**确定性 LLM 实现**与**进程内存储**，`pip install` 后即可完整运行。

接真实模型只需改一个环境变量：

```bash
export AIO_AGENTOS_LLM_PROVIDER=openai
export AIO_AGENTOS_LLM_API_KEY=sk-xxx
export AIO_AGENTOS_LLM_MODEL=deepseek-chat
```

---

## 它解决什么问题

AIOps 场景里，把 LLM 接进故障处置链路有四个必须回答的问题。本项目对每一个都给出了**可执行的答案**：

| 问题 | 本项目的答案 | 落点 |
|---|---|---|
| 模型会不会编造一个不存在的根因？ | 领域词表是本体的单一事实源，同时注入提示词 + 公理校验 + Agent 输出校验；越界值收敛到 `undetermined` | `ontology/domain.py`、`graph/nodes.py` |
| 高风险动作怎么保证不会自动执行？ | 审批门由**工具声明的风险等级**推导（不是写死的 if）；未审批时策略直接拒绝，且拒绝**不被重试掩盖** | `tools/catalog.py`、`tools/registry.py` |
| 重试会不会导致重复重启服务？ | 错误分级（瞬时/永久）+ 幂等键落库 + 资源锁互斥 | `tools/registry.py`、`temporal/workflow.py` |
| 出问题时怎么知道哪一步坏了？ | 用 `contextvars` 维护 Span 栈，Trace 是真正的父子树；异常路径也落盘，且跨重启可回读 | `observability/trace.py` |

---

## 架构一览

```text
┌──────────────────────────────────────────────────────────────────────┐
│  接入层    FastAPI (20 端点) · CLI · Temporal SDK Adapter             │
├──────────────────────────────────────────────────────────────────────┤
│  编排层    IncidentWorkflowService  ← 持久化外壳：状态机 + 审批挂起/恢复  │
│              │                                                       │
│              └─▶ IncidentReasoningGraph  ← 短时推理图（一次 Activity 内）│
│                    6 个节点 · 手写执行器与 LangGraph 执行器共用同一批节点 │
├──────────────────────────────────────────────────────────────────────┤
│  领域层    Agents(6) · Skills(6) · Router · Tools(9) · Governance      │
├──────────────────────────────────────────────────────────────────────┤
│  知识层    Ontology (只读 TBox) · Memory                              │
├──────────────────────────────────────────────────────────────────────┤
│  基础层    LLM 能力层 · Persistence · Resilience · Concurrency         │
│            Observability（Trace / Metrics / BadCase）                  │
└──────────────────────────────────────────────────────────────────────┘
```

**核心设计决策**（详见 `docs/architecture.md`）：

1. **Temporal 是外层持久化壳，LangGraph/推理图是内层短时推理**——两者是嵌套关系，不是串行流水线阶段。
2. **Ontology 是共享只读 TBox**，运行实例（告警、设备、执行记录）**不写入本体**。
3. **Governance 是 Tool interceptor**，策略检查位于重试循环**之外**。
4. **WorkflowState 是持久化编排字段的唯一事实源**；`AgentContext` 不跨 Agent 调用复用。
5. **提示词是需要被治理的资产**：有版本号、可灰度、可回滚，且 Span 上记录 `prompt_version`。
6. **依赖集中在组装根注入**（`bootstrap.py`），不在各层到处 `new`。

---

## 端到端纵向切片

```text
POST /alarm
   │
   ├─ Router: intent → skill → capability → agent → tool 白名单
   │
   ├─ 图节点 1  normalize_alarm   → 告警归一化（LLM 调用 ①）
   ├─ 图节点 2  collect_topology  → 工具 topology.lookup（声明式缓存，TTL 60s）
   ├─ 图节点 3  collect_logs      → 日志 → 结构化证据
   ├─ 图节点 4  reason            → 根因 + 置信度（LLM 调用 ②，受词表约束）
   ├─ 图节点 5  propose           → 修复动作（LLM 调用 ③，审批门来自工具风险）
   └─ 图节点 6  supervise         → 本体公理校验（越界即失败）
   │
   ├─ 高风险动作 → WAITING_APPROVAL（挂起，超时默认拒绝）
   │
POST /approval/{id}  ← 人工批准（职责分离：approver 无 tool:high 权限）
   │
   ├─ 资源锁 service:checkout（FIFO + 超时）
   ├─ 治理拦截：策略 / 权限 / 限流 / 幂等 / 熔断
   └─ ToolRegistry.invoke → COMPLETED
   │
   └─ Trace（父子树） + Episodic Memory + Metrics + BadCase
```

真实输出（`aio-agentos demo`，14 个 Span / 深度 4 / 2 个根节点）：

```text
调用树：
- [workflow] workflow.run (5.8ms)
  - [graph_node] graph.run (5.6ms)
    - [agent] agent.run (1.6ms)      ← alarm
      - [llm] llm.complete (1.0ms)
    - [agent] agent.run (1.6ms)      ← topology
      - [tool] tool.invoke (0.1ms)
    - [agent] agent.run (0.0ms)      ← log
    - [agent] agent.run (0.9ms)      ← reasoning
      - [llm] llm.complete (0.3ms)
    - [agent] agent.run (0.4ms)      ← remediation
      - [llm] llm.complete (0.1ms)
    - [governance] graph.supervise (0.0ms)
- [activity] workflow.remediation (0.4ms)   ← 第二个根：审批后才发生
  - [tool] tool.invoke (0.1ms)
```

为什么有两个根：`workflow.run` 覆盖推理阶段，它在 `WAITING_APPROVAL` 时已经返回；
审批与执行发生在那之后，不在它的上下文里。**Span 栈如实反映真实嵌套。**

---

## 能力清单

| 层 | 能力 | 数量 |
|---|---|---|
| 领域 | Agent（全部运行时可达，无孤立组件） | 6 |
| 领域 | Skill / Capability | 6 / 6 |
| 领域 | 受治理工具（低/中/高风险分级） | 9 |
| 知识 | 本体 TBox（类 / 属性 / 关系 / 公理） | 10 / 8 / 6 / 4 |
| 基础 | LLM provider（确定性 / OpenAI 兼容 / LangChain + 自动降级） | 3 + 降级 |
| 基础 | 存储后端（内存 / SQLite） | 2 |
| 基础 | 横切能力（限流 / 缓存 / 资源锁 / 熔断 / 幂等 / BadCase） | 6 |
| 接口 | HTTP 端点 | 20 |
| 质量 | 测试用例 / 分层契约 | 133 / 7 |

---

## API 速览

```bash
# 启动一次故障处置
curl -X POST localhost:8000/alarm -H 'Content-Type: application/json' -d '{
  "alarm_id": "A-1001", "service": "checkout", "severity": "critical",
  "logs": ["upstream timeout while calling payment"],
  "idempotency_key": "alarm-A-1001"
}'

# 查看状态 / 嵌套调用树 / 指标
curl localhost:8000/workflow/{id}
curl localhost:8000/workflow/{id}/trace/tree
curl localhost:8000/metrics

# 审批
curl -X POST localhost:8000/approval/{id} \
  -H 'Content-Type: application/json' \
  -d '{"approved": true, "approver": "oncall@example.com"}'

# 自描述：本体 / 技能 / Agent / 工具 / RBAC 矩阵
curl localhost:8000/ontology
curl localhost:8000/skills
curl localhost:8000/agents
curl localhost:8000/tools
curl localhost:8000/policies
```

完整参考见 [`docs/api-spec.md`](docs/api-spec.md)。

---

## 目录结构

```text
bootstrap.py         组装根：把配置/LLM/本体/技能/路由/工具/存储/横切能力装配成 Runtime
cli.py               运维入口：check / demo / serve
config.py            集中配置（所有环境变量读取只在这里）

ontology/            只读 TBox：领域词表 + 类/属性/关系/公理 + 版本兼容
memory/              Short/Long/Vector/Episodic 记忆记录与存储
observability/       Span(父子树) / Metrics / BadCaseCollector
runtime/             AgentContext + 生命周期状态机

llm/                 LLM 能力层：协议 + 版本化提示词 + 3 个 provider + 降级包装
persistence/         DocumentStore 抽象 + 内存/SQLite 实现
resilience/          令牌桶限流 + TTL/LRU 缓存
concurrency/         资源锁（FIFO 排队 + 超时）

governance/          风险等级 / RBAC / 策略拦截器
tools/               工具规格 + 默认目录 + 受治理执行注册表
skills/              Capability / Skill 契约 + AIOps 领域目录
router/              intent → skill → capability → agent → tool
agents/              6 个 Agent 实现 + 注册表
graph/               内层推理图：节点 + 手写执行器 + LangGraph 适配器
temporal/            外层持久化工作流：本地参考实现 + Temporal SDK 版本
protocol/            A2A AgentCard + MCP 工具目录
api/                 FastAPI 契约与端点

tests/               133 个用例（含端到端、回归、契约检查）
docs/                设计文档（见 docs/README.md）
```

---

## 配置

所有配置项与默认值见 [`.env.example`](.env.example)，完整说明见 [`docs/operations.md`](docs/operations.md)。

最常调整的几项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_LLM_PROVIDER` | `deterministic` | `deterministic` / `openai` / `deepseek` / `langchain` |
| `AIO_AGENTOS_STORE_BACKEND` | `memory` | `memory` / `sqlite`（后者跨重启保留状态） |
| `AIO_AGENTOS_API_KEY` | 未设置 | 设置后所有端点要求 `X-API-Key` |
| `AIO_AGENTOS_APPROVER_KEY` | 未设置 | 设置后审批端点额外要求 `X-Approver-Key` |
| `AIO_AGENTOS_APPROVAL_TIMEOUT_SECONDS` | `86400` | 审批超时后**默认拒绝** |

未配置密钥时系统仍可启动，但 `/healthz` 会把风险项列进 `security_warnings`——
**显式告警，而不是静默放行**。

---

## 质量门禁

CI 三道关卡，本地可一键复现：

```bash
ruff check .        # 静态检查：All checks passed
lint-imports        # 分层契约：7 kept, 0 broken
pytest -q           # 133 passed
```

`lint-imports` 固化了架构不变量，违反会直接让 CI 变红。例如：

- `observability` 不得依赖领域层或编排层（保证它可被任何一层安全引用）
- `ontology` / `memory` 不得依赖编排层
- `persistence` / `resilience` / `concurrency` 必须保持为基础设施叶子
- `governance` 是纯策略层，不得依赖 `tools` / `agents` / `graph` / `temporal`

---

## 设计文档

| 文档 | 内容 |
|---|---|
| [实现状态总览](docs/implementation-status.md) | 每个模块的实现程度、已交付与未交付清单 |
| [分层架构](docs/architecture.md) | 依赖方向、分层契约、装配根 |
| [状态所有权](docs/state-ownership.md) | 三套状态各归谁管，改代码时的红线 |
| [本体设计](docs/ontology-design.md) | 领域词表如何成为单一事实源 |
| [Agent 设计](docs/agent-design.md) | 6 个 Agent、生命周期、依赖注入 |
| [LLM 能力层](docs/llm-design.md) | 协议、提示词治理、provider 与降级 |
| [技能与路由](docs/skill-design.md) | 意图如何解析为执行契约 |
| [工具与治理](docs/security-design.md) | 风险分级、RBAC、职责分离、重试与幂等 |
| [记忆与持久化](docs/memory-design.md) | 四类记忆 + DocumentStore 抽象 |
| [可观测性与闭环](docs/observability-design.md) | Trace 树、指标、BadCase 闭环 |
| [弹性与横切能力](docs/resilience-design.md) | 限流、缓存、资源锁、熔断 |
| [API 参考](docs/api-spec.md) | 20 个端点的请求/响应契约 |
| [运行与配置](docs/operations.md) | CLI、环境变量、部署形态 |
| [评估](docs/evaluation.md) | 测试策略、回归基线、评估指标 |
| [历史审查记录](docs/history/) | 两轮架构审查与面试陈述审计的原始结论 |

---

## 边界说明

本项目是**参考实现**，不是生产系统。仓库**不会伪造外部基础设施或历史数据**。

以下属于部署/数据接入，已有清晰的 adapter 与契约边界，但不硬编码在源码里：

- Temporal Server / Namespace / TLS（`temporal/production.py`）
- 真实 MCP Tool endpoint、认证与基础设施执行器（`tools/catalog.py` 的 handler）
- OIDC / mTLS / 企业 IAM（`governance/policy.py` 的 principal 解析）
- PostgreSQL / 向量库实例与凭据（实现 `persistence.base.DocumentStore` 协议即可替换）
- ≥30 条真实脱敏历史故障 golden set（`tests/fixtures/`）
- Kafka / 告警平台 connector

`tools/catalog.py` 里的所有 handler 都返回 `mode: "reference"`，**不产生真实副作用**。
