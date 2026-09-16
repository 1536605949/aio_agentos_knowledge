# 评估

> 测试位置：[`tests/`](../tests/) ｜ 自检：[`cli.py`](../cli.py) 的 `cmd_check`
> 质量门禁配置：[`pyproject.toml`](../pyproject.toml)

---

## 一、测试策略

本项目是**参考实现**，因此测试的重心不是"覆盖所有分支"，而是：

> **每一个"原本有缺陷或完全缺失"的行为，都要有一条会失败的回归测试。**

`tests/test_tools_resilience.py` 的模块文档字符串把这一点写得很明确：

> 这些用例覆盖的都是**原实现存在缺陷或完全缺失**的行为，因此它们是回归保护的重点。

---

## 二、133 个用例的分布

```bash
pytest --collect-only -q | tail -1     # 133 tests collected
```

| 文件 | 用例数 | 覆盖主题 |
|---|---:|---|
| [`test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py) | 19 | 孤立组件检查、路由、词表单一事实源、公理、Agent 收敛、审批门 |
| [`test_api_extended.py`](../tests/test_api_extended.py) | 18 | 20 个端点、嵌套调用树、审批前后风险对比、拒绝、反馈、鉴权、限流 |
| [`test_tools_resilience.py`](../tests/test_tools_resilience.py) | 17 | 治理位置、审批者越权、重试分级、熔断、幂等 + store、缓存、限流、资源锁、BadCase |
| [`test_workflow_lifecycle.py`](../tests/test_workflow_lifecycle.py) | 16 | 状态机、本体门禁、超时默认拒绝、持久化恢复、职责分离、资源锁 |
| [`test_llm.py`](../tests/test_llm.py) | 16 | JSON 抽取 5 种格式、提示词版本与渲染、确定性实现、降级统计 |
| [`test_observability.py`](../tests/test_observability.py) | 12 | Span 树/兄弟/异常落盘/栈复位、指标、BadCase 全路径、并发安全 |
| [`test_resilience.py`](../tests/test_resilience.py) | 10 | 令牌桶 burst/隔离/refill、TTL 过期、LRU 淘汰、prefix 失效 |
| [`test_persistence.py`](../tests/test_persistence.py) | 6 | 内存/SQLite 往返、**重开文件验证跨重启**、后端选择 |
| [`test_concurrency.py`](../tests/test_concurrency.py) | 5 | 互斥、FIFO 公平、超时、统计、不同资源不阻塞 |
| [`test_cli.py`](../tests/test_cli.py) | 5 | `check` 返回 0、无 `BUG` 标记、Trace 层级 |
| [`test_workflow.py`](../tests/test_workflow.py) | 3 | 工作流基础行为 |
| [`test_ontology.py`](../tests/test_ontology.py) | 2 | 本体模型与版本 |
| [`test_router.py`](../tests/test_router.py) | 1 | 路由基础行为 |
| [`test_governance_tools.py`](../tests/test_governance_tools.py) | 1 | 策略与工具基础行为 |
| [`test_api.py`](../tests/test_api.py) | 1 | 基础端点 |
| [`test_agents.py`](../tests/test_agents.py) | 1 | Agent 基础行为 |
| **合计** | **133** | |

### 测试隔离

[`tests/conftest.py`](../tests/conftest.py) 提供一个 autouse fixture，在每个用例前后重置配置缓存与运行时单例：

```python
@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    reset_settings_cache()
    yield
    reset_settings_cache()
```

以及一个收集并关闭服务实例的 fixture，避免审批超时任务泄漏：

```python
@pytest.fixture(autouse=True)
async def _close_services():
    yield
    for service in _CREATED_SERVICES:
        await service.close()
```

不加这一步，测试结束时会报 `Task was destroyed but it is pending!`。

---

## 三、回归基线：15 项关键缺陷

下表每一项都有对应的回归测试。**这是本项目测试体系最核心的部分。**

| # | 原缺陷 | 回归测试 | 文件 |
|---|---|---|---|
| 1 | 41 个 ruff 违规，CI 从未跑通 | CI 三道关卡 | `pyproject.toml` |
| 2 | `router/` + `skills/` 运行时零引用 | 孤立组件检查 | `test_router_skills_ontology.py` |
| 3 | `ontology/` 只有模型没有领域定义 | 词表三处消费一致性 | `test_router_skills_ontology.py` |
| 4 | 零 LLM 调用 | 3 个调用点 + `llm.complete` Span | `test_llm.py`、`test_cli.py` |
| 5 | 零提示词模板 | 版本与渲染校验 | `test_llm.py` |
| 6 | 审批门由启发式推导 | 审批门来自工具风险 | `test_router_skills_ontology.py` |
| 7 | **策略拒绝被重试 3 次** | `test_policy_denial_is_not_masked_by_retries` | `test_tools_resilience.py` |
| 8 | 重试导致副作用重复 | 幂等键 + 错误分级 | `test_tools_resilience.py` |
| 9 | **`approver` 越权执行** | `test_approver_role_cannot_execute_high_risk_tool` | `test_tools_resilience.py` |
| 10 | Span 全扁平（`parent_span_id` 恒为 `None`） | `test_spans_nest_into_a_tree`、`max_depth` 断言 | `test_observability.py`、`test_cli.py` |
| 11 | 零持久化后端 | 重开文件验证跨重启 | `test_persistence.py` |
| 12 | 零 BadCase 生产者 | BadCase 全路径 + 10 类来源 | `test_observability.py`、`test_tools_resilience.py` |
| 13 | 限流/缓存/资源锁/熔断全缺失 | 各自独立用例 | `test_resilience.py`、`test_concurrency.py`、`test_tools_resilience.py` |
| 14 | `protocol/` 模型孤立 | AgentCard / MCP 目录从注册表生成 | `test_api_extended.py` |
| 15 | `langgraph_adapter` 与 `pipeline` 语义分叉 | 共用同一批节点函数 | `test_workflow_lifecycle.py` |

**第 7 与第 9 项是安全相关的**，它们的断言方式值得单独看：

```python
# #7：策略拒绝时 handler 一次都不能被调用
assert calls == []
assert registry.stats()["service.restart"]["policy_denials"] == 1

# #9：即使 approved=True，approver 也不能执行
with pytest.raises(ToolPolicyDenied) as excinfo:
    await registry.invoke("service.restart", {}, APPROVER, AgentTrace(), approved=True)
assert "tool:high" in str(excinfo.value)
```

---

## 四、三道质量门禁

```bash
ruff check .        # 静态检查
lint-imports        # 分层契约
pytest -q           # 单元 + 集成 + 端到端
```

### 1. `ruff check .`

配置在 `pyproject.toml`：

```toml
[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501"]
```

选中的规则组：`E`（pycodestyle）、`F`（pyflakes）、`I`（isort）、`B`（bugbear）、`UP`（pyupgrade）。

**`UP` 组会强制使用现代语法**——例如 `str, Enum` 会被要求改成 `StrEnum`（Python 3.11+）。
这也是 `MemoryType`、`AgentLifecycle`、`Role`、`WorkflowStatus` 等枚举都用 `StrEnum` 的原因。

### 2. `lint-imports`

7 条分层契约，违反直接让 CI 变红：

| # | 契约 | 约束 |
|---|---|---|
| 1 | Observability 是叶子 | `observability` 不得依赖领域层或编排层 |
| 2 | LLM 层是叶子 | `llm` 不得依赖领域层、编排层、持久化 |
| 3 | Resilience / Concurrency 是叶子 | 两者不得依赖除自身外的任何包 |
| 4 | Persistence 是叶子 | `persistence` 不得依赖领域层与编排层 |
| 5 | Ontology / Memory 只读 | 不得依赖编排层与 `tools` / `agents` / `router` / `skills` |
| 6 | Protocol 不被知识层引用 | `ontology` / `memory` 不得依赖 `protocol` |
| 7 | Governance 是纯策略层 | `governance` 不得依赖 `agents` / `graph` / `temporal` / `api` / `router` / `skills` / `tools` |

**契约的作用是防止架构腐化**：它们把"依赖方向"这个抽象原则变成 CI 会拒绝的具体规则。
详见 [分层架构](architecture.md#三分层契约)。

### 3. `pytest -q`

133 个用例，全部零外部依赖——**没有任何一个需要网络、API Key 或数据库**。

---

## 五、`aio-agentos check`：行为级自检

pytest 验证的是"单元行为正确"，`check` 验证的是"**装配起来真的能跑**"。

```bash
aio-agentos check
```

它会真的调用，而不是只 import：

| 步骤 | 断言 |
|---|---|
| 工具探针 | 低风险放行；高风险未审批被拒；重试后成功；永久错误不重试 |
| 幂等重放 | 同 key 两次调用结果相同 |
| 端到端 | `status == "completed"` |
| 本体校验 | `ontology_errors == []` |
| Trace 结构 | `max_depth >= 2`（不是扁平） |

实测输出：

```text
=== 工具探针 ===
{
  "low_risk_ok": {"service": "checkout", "dependencies": ["payment", "inventory", "cart"]},
  "high_risk_without_approval": "ToolPolicyDenied: human approval required",
  "high_risk_with_approval": {"action": "restart_service", "executed": true, "mode": "reference"},
  "retry_succeeded": {"attempts": 3, "ok": true},
  "permanent_error_not_retried": "ToolError",
  "idempotent_replay_same_result": true
}

=== 端到端 ===
{
  "status": "completed",
  "root_cause": "upstream_timeout",
  "confidence": 0.7,
  "proposed_action": "restart_service",
  "action_tool": "service.restart",
  "ontology_errors": [],
  "steps": ["normalize_alarm", "collect_topology", "collect_logs", "reason", "propose", "supervise"],
  "trace_summary": {"span_count": 14, "max_depth": 4, "root_span_count": 2, "error_span_count": 0}
}

自检通过。
```

退出码 `0` / `1`，可直接作为部署前置门禁。

---

## 六、评估指标（设计与现状）

### 已在代码中声明的指标

`AgentSpec.evaluation_metrics` 每个 Agent 都声明了两项：

```python
evaluation_metrics=[
    EvaluationMetric(name="schema_validity", target=1.0),
    EvaluationMetric(name="ontology_conformance", target=1.0),
]
```

| 指标 | 含义 | 当前验证方式 |
|---|---|---|
| `schema_validity` | 输出是否满足声明的 `output_schema` | `BaseAgent.validate_output()` 在运行时强制 |
| `ontology_conformance` | 输出是否落在本体词表内 | Agent 收敛逻辑 + `supervise` 公理校验 |

**注意 `target=1.0` 是硬约束而非统计目标**——这两个指标不达标意味着**流程失败**，
而不是"质量下降"。

### `EvalResult` 数据结构

```python
class EvalResult(BaseModel):
    case_id: str
    metric: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)
```

**这个结构定义了但当前没有生产者**——它是为离线评估集预留的接口。

### 生产验收需要的指标

以下指标**需要真实历史数据才能计算**，本项目不伪造：

| 指标 | 定义 | 需要什么 |
|---|---|---|
| 根因准确率 | 判定根因与人工标注一致的比例 | ≥30 条真实脱敏历史故障 |
| 动作采纳率 | 建议动作被运维采纳的比例 | 工单系统对接 |
| 人工干预率 | 需要人工介入的处置占比 | 审批记录统计 |
| 平均恢复时间（MTTR） | 从告警到恢复的时长 | 告警平台 + 监控对接 |
| 单次成本 | 每次处置的 LLM token 成本 | 真实 provider 的价目表 |

**为什么不做 synthetic golden set**：
用模型生成的"历史故障"来评估模型，是一个自我循环的评估，结论没有意义。

---

## 七、BadCase → 回归集的工作流

```text
生产运行
   │
   ▼
BadCaseCollector（10 类来源）
   │
   ▼
GET /badcases?unresolved_only=true
   │
   ▼
人工标注：正确的根因 / 可接受的动作
   │
   ▼
回归集（tests/fixtures/）
   │
   ▼
CI 门禁：新增用例必须通过
```

**当前状态**：前四步已就绪（BadCase 采集 + 查询 + `resolve()` 标记 + JSONL 导出），
**第五步（回归集 fixtures）未交付**——需要真实数据。

---

## 八、边界与诚实声明

`tests/fixtures/` 目录下的数据（若有）**只用于代码回归**，不代表生产质量。
本项目**不会伪造外部基础设施或历史数据**：

- 所有工具 handler 返回 `mode: "reference"`，**不产生真实副作用**
- 没有 synthetic golden set 冒充真实评估集
- `LLMUsage.cost_usd` 恒为 0，不内置任何厂商价格表
- 没有"准确率 95%"这类无法验证的宣称

**可以验证的结论**：

```bash
ruff check .        # All checks passed!
lint-imports        # Contracts: 7 kept, 0 broken
pytest -q           # 133 passed
aio-agentos check   # 自检通过
```

---

## 未交付

- **真实脱敏历史故障 golden set**（≥30 条）：需要真实数据接入。
- **`EvalResult` 的生产者**：数据结构已定义，没有离线评估作业。
- **回归集 fixtures**：`tests/fixtures/` 尚未建立。
- **覆盖率报告**：没有接入 `pytest-cov`，也没有覆盖率门禁。
- **性能/压测**：没有负载测试，没有吞吐/延迟基线。
- **混沌测试**：没有故障注入框架（虽然 `demo.flaky` 工具可用于手工验证重试与熔断）。
- **LLM 输出质量评估**：`schema_validity` 与 `ontology_conformance` 是结构校验，
  **不评估根因判断是否正确**——这需要人工标注数据。
- **A/B 对比**：没有"两版提示词在同一批 case 上的对比"机制。
