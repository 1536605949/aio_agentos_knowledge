# 01_architecture_review 落地报告

本次补齐以审查报告的 W1–W13 与 Phase 0–5 为执行清单。

| 审查项 | 落地 |
|---|---|
| W1 Temporal 拓扑错误 | 改为 Temporal 外壳 + Graph 内核；新增 `docs/architecture.md`、`temporal/production.py` |
| W2 状态所有权 | 新增 `docs/state-ownership.md`；`WorkflowState` 成为持久化编排字段事实源 |
| W3 文档/实现失衡 | 核心名词全部升级为 Pydantic Schema 或可执行逻辑 |
| W4 工程基线 | `pyproject.toml`、包初始化、CI、Docker、`.gitignore`、测试 |
| W5 横切层误建模 | Ontology/Memory/Observability/Governance 改为共享层/拦截器 |
| W6 契约缺失 | API、AgentSpec、SkillSpec、ToolSpec、AgentCard、MCP descriptor、Trace models |
| W7 Tool/Router 缺失 | 新增 `tools/`、`skills/`、`router/` |
| W8 失败/幂等 | API idempotency；Tool timeout/retry/circuit breaker；审批拒绝/超时 |
| W9 Evaluation | 新增 `EvalResult`、synthetic regression fixture、`docs/evaluation.md` |
| W10 安全不闭环 | 四级风险、RBAC、Policy interceptor、审批 credential、默认拒绝 |
| W11 版本演进 | `OntologyVersion` + semver/compatibility 约束 |
| W12 领域命名 | README 明确项目是 AIOps；交付根目录保持原项目名以兼容引用 |
| W13 V3 基线不可追溯 | `docs/00_upgrade_review.md` 改为“可执行交付基线”，不伪造不存在的 V3 事实 |

## 已验证

- `pytest -q`：9 passed
- `python -m compileall -q .`：通过
- `python -m examples.demo`：可完成 WAITING_APPROVAL -> approval -> governed tool -> COMPLETED，并产生 Trace + Episodic Memory

## 需要生产环境提供

仓库不会伪造外部基础设施或历史数据。以下属于部署/数据接入，不应硬编码在源码：

- Temporal Server / Namespace / TLS
- 真实 MCP Tool endpoint、认证与基础设施执行器
- OIDC/mTLS/企业 IAM
- PostgreSQL/VectorDB 实例及凭据
- ≥30 条真实、脱敏历史故障 golden set
- Kafka/Alarm 平台实际 connector

这些都已有清晰 adapter/契约边界，可在不改变核心状态所有权的前提下接入。
