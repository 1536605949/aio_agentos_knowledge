# AIO-AgentOS v3.5

AIO-AgentOS v3.5 是一个面向 **AIOps 故障根因分析与受治理修复** 的可运行参考实现。它从原先的“知识骨架”补齐为：可执行 Schema、Route-Skill Router、Tool Registry、Policy/RBAC、内层推理图、外层持久化工作流语义、HITL 审批、Memory、Trace、API、测试和工程基线。

## 架构原则

- **Temporal 是外层持久化编排壳**：持有跨进程/长时间的 Workflow 状态、重试、审批等待与恢复。
- **LangGraph/Reasoning Graph 是内层短时推理图**：在 Activity 内完成证据收集、根因推理、动作建议。
- **Governance 是 Tool interceptor**：每次 Tool 调用都执行 Policy/RBAC/Risk 检查，不是流水线末端阶段。
- **Ontology 是共享只读 TBox**；ABox/运行实例不写入 Ontology。
- **Memory 与 Observability 是横切能力**：节点执行期间持续读写，失败路径也保留 Trace。
- **WorkflowState 是持久化编排字段的唯一事实源**；AgentContext 仅是单次 Agent 调用的临时上下文。

## 目录

```text
ontology/       TBox Schema、Axiom、Version
memory/         Short/Long/Vector/Episodic record + store
observability/  Span、Metric、Evaluation、Feedback、Trace recorder
runtime/        AgentContext + lifecycle
governance/     Risk、RBAC、Policy Engine
tools/          ToolSpec、registry、timeout/retry/circuit breaker
skills/         CapabilitySpec、SkillSpec、registry
router/         Intent -> Skill -> Capability -> Agent -> Tool -> Workflow
agents/         6 个 Agent 的规范与参考实现
graph/          内层 reasoning graph + LangGraph adapter
temporal/       外层 workflow state、HITL、本地服务、Temporal SDK adapter
protocol/mcp/   出站 Tool/Resource 目录
protocol/a2a/   入站 AgentCard
api/            FastAPI 契约与端点
tests/          契约、路由、治理、工作流、API 测试
docs/           状态所有权、安全、API、架构流向等
```

## 快速运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
uvicorn api.app:app --reload
```

生产编排依赖单独安装：

```bash
pip install -e ".[orchestration]"
```

本地 demo 不要求 Temporal Server，也不会真实重启服务；`service.restart` 是安全的 demo handler。生产环境应将它替换为经过 MCP/基础设施适配器的真实 Activity。

## API

- `POST /alarm`：启动故障 Workflow；支持 `idempotency_key`。
- `GET /workflow/{id}`：读取 Workflow 的持久化状态。
- `POST /approval/{id}`：对高风险动作批准/拒绝。
- `GET /workflow/{id}/trace`：读取完整 Trace。

如设置 `AIO_AGENTOS_API_KEY`，API 必须通过 `X-API-Key` 访问。

## 纵向切片

默认 demo 路径：

```text
Alarm
  -> outer Workflow
  -> Log Agent
  -> Reasoning Agent
  -> Remediation Agent proposes action
  -> Policy/HITL gate
  -> approval signal
  -> governed Tool invocation
  -> Trace + Episodic Memory
```

详见 `docs/state-ownership.md`、`docs/architecture.md`。可用 `python -m examples.demo` 运行本地纵向切片。
