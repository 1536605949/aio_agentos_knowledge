# 实现状态总览

> 本文档回答一个问题：**每个模块到底做到什么程度？**
> 判定标准只有一条——能否在 `aio-agentos check` 或 `pytest` 中找到对应验证。

最后更新：对应 `main` 分支 133 passed / 7 contracts kept / ruff clean。

---

## 一、总览

| 维度 | 数值 |
|---|---|
| Python 源文件 | 79 |
| 代码行数 | ~9,100（含测试） |
| 测试用例 | 133 |
| 分层契约 | 7 |
| HTTP 端点 | 20 |
| Agent / Skill / Capability / Tool | 6 / 6 / 6 / 9 |
| 本体 TBox | 10 类 / 8 属性 / 6 关系 / 4 公理 |

---

## 二、逐模块状态

图例：**✅ 完整** = 有实现 + 有测试 + 在运行路径上可达；
**◐ 参考实现** = 逻辑完整但外部依赖以无副作用的参考实现代替；
**⬜ 边界** = 刻意不做，属于部署/接入层。

### 知识层

| 模块 | 状态 | 说明 | 验证 |
|---|---|---|---|
| `ontology/domain.py` | ✅ 完整 | 领域词表唯一定义点：6 根因 / 5 严重级别 / 6 动作；10 类 / 8 属性 / 6 关系 / 4 公理 | `test_router_skills_ontology.py`、`GET /ontology` |
| `ontology/models.py` | ✅ 完整 | TBox 注册表、公理校验、semver 兼容判定 | `test_ontology.py` |
| `memory/types.py` | ✅ 完整 | Short / Long / Vector / Episodic 四类记录 | `test_workflow_lifecycle.py` |
| `memory/store.py` | ◐ 参考实现 | 线程安全进程内存储；生产替换为向量库/DB | `test_workflow_lifecycle.py` |

### 基础设施层

| 模块 | 状态 | 说明 | 验证 |
|---|---|---|---|
| `llm/base.py` | ✅ 完整 | `LLMClient` 协议 + 稳健 JSON 抽取（整体解析 / 去围栏 / 平衡括号） | `test_llm.py`（5 种真实模型输出格式） |
| `llm/prompts.py` | ✅ 完整 | 版本化提示词注册表，6 个默认模板，缺变量即报错 | `test_llm.py` |
| `llm/deterministic.py` | ✅ 完整 | 确定性实现，作为 CI 基线与无凭据环境的兜底 | `test_llm.py` |
| `llm/openai_compatible.py` | ◐ 参考实现 | httpx 调用 `/chat/completions`，5xx/429 指数退避重试 | 构建路径 `test_llm.py`；真实调用需凭据 |
| `llm/langchain_adapter.py` | ◐ 参考实现 | 可选依赖，未安装时自动回退 | `langchain_available()` |
| `llm/factory.py` | ✅ 完整 | `ResilientLLM` 降级包装 + 用量统计 | `test_llm.py` |
| `persistence/base.py` | ✅ 完整 | `DocumentStore` 协议 + 内存实现 | `test_persistence.py` |
| `persistence/sqlite_store.py` | ✅ 完整 | 标准库 sqlite3 + WAL + UPSERT 保留 created_at | `test_persistence.py`（含重开文件验证） |
| `resilience/ratelimit.py` | ✅ 完整 | 惰性补充令牌桶，按 key 隔离 | `test_resilience.py` |
| `resilience/cache.py` | ✅ 完整 | TTL + LRU，命中率统计 | `test_resilience.py` |
| `concurrency/locks.py` | ✅ 完整 | 资源锁，FIFO 排队 + 超时 | `test_concurrency.py`（含 FIFO 顺序断言） |
| `observability/trace.py` | ✅ 完整 | `contextvars` Span 栈，`tree()` / `summary()`，跨重启回读 | `test_observability.py` |
| `observability/metrics.py` | ✅ 完整 | 计数器 / 仪表 / 时长分布（p50/p95/max/avg） | `test_observability.py` |
| `observability/badcase.py` | ✅ 完整 | 10 类 BadCase，落库 + JSONL 导出 + 分类汇总 | `test_observability.py` |

### 领域层

| 模块 | 状态 | 说明 | 验证 |
|---|---|---|---|
| `governance/policy.py` | ✅ 完整 | 4 角色 RBAC 矩阵、4 级风险判定、职责分离 | `test_tools_resilience.py`、`GET /policies` |
| `tools/models.py` | ✅ 完整 | 工具规格：风险 / 超时 / 重试 / 缓存 / 幂等 / 资源字段 | `test_tools_resilience.py` |
| `tools/catalog.py` | ✅ 完整 | 9 个 AIOps 工具 + 动作→工具映射 + 动作风险派生 | `test_router_skills_ontology.py` |
| `tools/registry.py` | ✅ 完整 | 10 步调用链：治理 → 权限 → 限流 → 幂等 → 缓存 → 资源锁 → 分级重试 → 熔断 → BadCase | `test_tools_resilience.py`（17 个用例） |
| `skills/catalog.py` | ✅ 完整 | 6 能力 + 6 技能 + 意图词表 | `test_router_skills_ontology.py` |
| `router/router.py` | ✅ 完整 | 意图解析 + 默认回落 + 流水线计划 | `test_router.py`、`GET /router/plan` |
| `agents/implementations.py` | ✅ 完整 | 6 个 Agent，全部在运行路径上可达 | `test_router_skills_ontology.py`（孤立组件检查） |
| `agents/base.py` | ✅ 完整 | 生命周期 + Span + LLM 助手 + 输出 schema 校验 | `test_router_skills_ontology.py` |
| `graph/nodes.py` | ✅ 完整 | 6 个节点，手写执行器与 LangGraph 共用同一批函数 | `test_workflow_lifecycle.py` |
| `graph/pipeline.py` | ✅ 完整 | 短时推理图执行器 | `test_workflow_lifecycle.py` |
| `graph/langgraph_adapter.py` | ◐ 参考实现 | 真条件边（3 路分支），需 `[orchestration]` extra | `langgraph_available()` |
| `temporal/workflow.py` | ✅ 完整 | 持久化外壳：状态机 + 审批挂起/恢复 + 幂等 + 资源锁 + BadCase | `test_workflow_lifecycle.py`（18 个用例） |
| `temporal/production.py` | ◐ 参考实现 | Temporal SDK 版本，与本地实现共享领域组件 | 需 Temporal Server |
| `protocol/catalog.py` | ✅ 完整 | 从 Agent/工具注册表生成 AgentCard 与 MCP 目录 | `GET /.well-known/agent-card.json` |
| `api/app.py` | ✅ 完整 | 20 端点 + 鉴权 + 限流依赖 | `test_api.py`、`test_api_extended.py`（18 个用例） |

### 装配与入口

| 模块 | 状态 | 说明 |
|---|---|---|
| `bootstrap.py` | ✅ 完整 | 组装根：一个 `Runtime` 承载全部可替换依赖 |
| `config.py` | ✅ 完整 | 集中配置 + `validate_security_posture()` |
| `cli.py` | ✅ 完整 | `check` / `demo` / `serve` |

---

## 三、本轮修复的关键缺陷

这些是**原实现里真实存在、且已通过行为测试复现**的问题。

| # | 缺陷 | 影响 | 修复 | 回归测试 |
|---|---|---|---|---|
| 1 | 41 个 ruff 违规，CI 从未跑通 | 质量门禁形同虚设 | 全部修复，`ruff check .` 退出码 0 | CI |
| 2 | `router/` + `skills/` 运行时零引用 | 整条路由链路是死代码 | 补齐技能目录并在 `start()` 中真实路由 | `test_every_skill_capability_has_an_agent` |
| 3 | `ontology/` 只有模型没有领域定义 | "本体驱动"无法落地 | 新增 `domain.py`，词表成为单一事实源 | `test_ontology_vocabulary_is_single_source_of_truth` |
| 4 | 零 LLM 调用 | Agent 只是硬编码 if/elif | 引入 LLM 能力层，3 个调用点 | `GET /metrics` → `llm.calls` |
| 5 | 零提示词模板 | 提示词资产无法治理 | 版本化注册表，Span 记录 `prompt_version` | `test_default_registry_contains_all_agent_prompts` |
| 6 | 审批门由启发式推导（`action == "restart_service"`） | 新增高风险动作易漏判 | 改由工具声明的风险等级驱动 | `test_remediation_agent_derives_approval_from_tool_risk` |
| 7 | 策略拒绝被当成瞬时故障重试 3 次 | 越权调用被反复尝试 | 治理检查移出重试循环 | `test_policy_denial_is_not_masked_by_retries` |
| 8 | 重试导致副作用重复执行 | 服务被重复重启 | 错误分级 + 幂等键落库 | `test_idempotent_replay_does_not_re_execute` |
| 9 | `approver` 角色可通过 `or "approval:decide"` 获得执行权 | 职责分离被破坏 | 只认 `tool:high` | `test_approver_role_cannot_execute_high_risk_tool` |
| 10 | Span 全部扁平（`parent_span_id` 恒为 None） | Trace 无法定位失败层级 | `contextvars` 维护 Span 栈 | `test_spans_nest_into_a_tree` |
| 11 | 零持久化后端，重启丢状态 | 幂等与审批状态不可靠 | `DocumentStore` + SQLite 实现 | `test_state_is_persisted_and_readable_by_a_fresh_service` |
| 12 | 零 BadCase 生产者 | 迭代闭环没有输入 | 10 类来源全部接入 | `test_failure_is_archived_as_badcase` 等 |
| 13 | 限流 / 缓存 / 资源锁 / 熔断全缺失 | 高峰与并发场景无保护 | 4 项全部实现并接入调用路径 | `test_resilience.py`、`test_concurrency.py` |
| 14 | `protocol/` 模型孤立无生产者 | 对外能力无法被发现 | 从注册表生成 AgentCard / MCP 目录 | `test_protocol_endpoints` |
| 15 | `langgraph_adapter` 与 `pipeline` 各写一套推理逻辑 | 两套实现语义分叉 | 共用同一批节点函数 | `graph/nodes.py` 单一来源 |

---

## 四、明确未交付（边界）

这些不是"待办"，而是**刻意留在部署/接入层**的东西。仓库不伪造它们。

| 项 | 归属 | 接入点 |
|---|---|---|
| Temporal Server / Namespace / TLS | 部署 | `temporal/production.py`、`temporal/sdk_worker.py` |
| 真实基础设施执行器（重启服务、回滚配置） | 接入 | 替换 `tools/catalog.py` 中的 handler |
| MCP Tool 真实 endpoint 与认证 | 接入 | `protocol/mcp/client.py` |
| OIDC / mTLS / 企业 IAM | 接入 | `governance` 的 principal 解析 |
| PostgreSQL / 向量库 | 接入 | 实现 `persistence.base.DocumentStore` 协议 |
| Kafka / 告警平台 connector | 接入 | `POST /alarm` 的上游 |
| ≥30 条真实脱敏历史故障 golden set | 数据 | `tests/fixtures/` |
| 分布式限流（Redis 实现） | 部署 | 实现 `TokenBucketLimiter` 同接口 |
| OTel / Prometheus 导出 | 部署 | `observability/metrics.py` 已用 `aio_agentos_` 前缀规范化键名 |

---

## 五、自检怎么跑

```bash
aio-agentos check
```

它会真的执行并断言：

- 配置与安全姿态（含未配置密钥的显式告警）
- 本体 TBox 完整性
- 路由计划能解析出完整技能链
- 工具四条路径：低风险成功 / 高风险无审批被拒 / 瞬时错误重试成功 / 永久错误不重试
- 幂等重放结果一致
- 端到端：告警 → 推理 → 审批 → 受治理执行 → `completed`
- 本体校验无错误、Trace 层级深度 ≥ 2
- BadCase 与指标确实被写入

任何一项失败，命令返回非 0 并列出具体原因。
