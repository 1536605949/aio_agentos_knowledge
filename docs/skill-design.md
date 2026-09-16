# 技能与路由

> 代码位置：[`skills/`](../skills/)、[`router/`](../router/)
> 领域目录：[`skills/catalog.py`](../skills/catalog.py) ｜ 路由：[`router/router.py`](../router/router.py)

---

## 一、Capability 与 Skill 不是同义词

这是本项目里最容易被混淆的一对概念：

| 概念 | 回答什么问题 | 特性 | 例子 |
|---|---|---|---|
| **Capability** | "系统**能**做什么" | 稳定的能力分类，无版本、无风险、无权限 | `root_cause_reasoning` |
| **Skill** | "**怎么**做这件事、谁来做、能用什么工具、有多危险" | 可版本化、可治理、可路由的业务契约 | `infer_root_cause` |

```python
class CapabilitySpec(BaseModel):
    name: str
    description: str = ""

class SkillSpec(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    intents: list[str] = Field(default_factory=list)        # 触发意图
    required_capability: str                                # 绑定能力
    allowed_agents: list[str] = Field(default_factory=list) # 执行者白名单
    allowed_tools: list[str] = Field(default_factory=list)  # 工具权限边界
    risk_level: RiskLevel = RiskLevel.LOW
    workflow_template: str = "incident_response"
```

**Capability 是名词，Skill 是带约束的动词。**

一个 Capability 可以被多个 Skill 复用（例如"根因推理"能力可以有一个快速版和一个深度版 Skill），
但每个 Skill 必须绑定恰好一个 Capability。

---

## 二、六个 Capability 与六个 Skill

```text
Capability（能做什么）              Skill（怎么做）                执行者
────────────────────────────────────────────────────────────────────────
alarm_normalization      ──────▶  normalize_alarm      ──────▶  alarm
topology_evidence        ──────▶  collect_topology     ──────▶  topology
log_evidence             ──────▶  collect_logs         ──────▶  log
root_cause_reasoning     ──────▶  infer_root_cause     ──────▶  reasoning
remediation              ──────▶  remediate_incident   ──────▶  remediation
ticketing                ──────▶  create_ticket        ──────▶  ticket
```

### 逐个说明

| Skill | 版本 | 风险 | intents | allowed_tools |
|---|---|---|---|---|
| `normalize_alarm` | 1.0.0 | LOW | `alarm`、`normalize_alarm`、`alert`、`告警`、`标准化` | — |
| `collect_topology` | 1.0.0 | LOW | `topology`、`dependency`、`拓扑`、`依赖` | `topology.lookup` |
| `collect_logs` | 1.0.0 | LOW | `logs`、`log_evidence`、`日志`、`取证` | `logs.search` |
| `infer_root_cause` | 1.0.0 | LOW | `diagnose`、`root_cause`、`reason`、`根因`、`诊断` | — |
| `remediate_incident` | 1.0.0 | **HIGH** | `remediate`、`fix`、`repair`、`修复`、`恢复` | `service.restart`、`config.rollback`、`service.scale_out` |
| `create_ticket` | 1.0.0 | LOW | `ticket`、`escalate`、`工单`、`升级` | `ticket.create` |

**中英文意图都支持**——这不是多余的，AIOps 场景里告警平台的标签经常是中文。

---

## 三、路由：意图 → 执行契约

[`router/router.py`](../router/router.py) 把意图解析成一条**显式的执行契约**：

```python
class RouteDecision(BaseModel):
    intent: str
    skill: str
    capability: str
    agent: str
    tools: list[str]
    workflow_template: str
```

### 为什么这比"直接调 Agent"好

因为它让"Agent 能用哪些工具"成为**可审计的静态声明**，
而不是散落在代码里的隐式调用。三处收益：

1. **权限边界可复核**：`allowed_tools` 是声明式的，治理层可以独立复核
2. **可自描述**：`GET /router/route` 让外部系统在调用前就知道会发生什么
3. **可演进**：新增意图只需注册 Skill，不用改路由代码

### 三个方法

```python
def route(self, intent: str) -> RouteDecision:
    """按意图路由；无匹配时抛 LookupError。"""
    matches = self.registry.for_intent(intent)
    if not matches:
        raise LookupError(f"no skill registered for intent: {intent}")
    # 同名意图可能命中多个 Skill，取风险最低的一个作为默认路径
    skill: SkillSpec = sorted(matches, key=lambda spec: int(spec.risk_level))[0]
    if not skill.allowed_agents:
        raise LookupError(f"skill has no allowed agent: {skill.name}")
    return RouteDecision(...)
```

**风险最低优先**这条规则值得注意：如果同一个意图被多个 Skill 声明（例如 `fix`
同时匹配一个"仅诊断"Skill 和一个"自动修复"Skill），路由会选低风险的那个。
**安全的默认行为不需要调用方额外指定。**

```python
def route_or_default(self, intent: str | None = None) -> RouteDecision:
    """路由失败时回落到默认意图，保证入口永远可用。"""
    try:
        return self.route(intent or DEFAULT_INTENT)
    except LookupError:
        return self.route(DEFAULT_INTENT)
```

`route_or_default` 是**工作流启动时实际调用的方法**（见 [`temporal/workflow.py`](../temporal/workflow.py) 的 `start`）。
传入未知意图不会让请求失败，而是回落到 `diagnose`——**入口的可用性优先于严格的意图校验**。

```python
def plan(self, intent: str = DEFAULT_INTENT) -> list[RouteDecision]:
    """返回端到端处理一次故障的完整技能链。"""
```

`plan()` 返回 `INCIDENT_PIPELINE` 定义的四步：

```python
INCIDENT_PIPELINE = (
    "normalize_alarm",
    "collect_logs",
    "infer_root_cause",
    "remediate_incident",
)
```

注意这里**不含 `collect_topology`**——拓扑取证是可选的增强证据，
不在"处理一次故障的最小必要链路"里。

### 辅助查询

```python
def intents(self) -> list[str]:
    return sorted({intent for spec in self.registry.skills.values() for intent in spec.intents})

def describe(self) -> list[dict[str, object]]:
    ...   # 供 GET /skills 端点
```

---

## 四、路由与图的关系

一个容易产生的疑问：路由已经决定了"用哪个 Agent"，那图的六个节点和它是什么关系？

```text
路由（意图层）              图（执行层）
─────────────────────────────────────────────────
intent=diagnose
   │
   └─▶ infer_root_cause
        └─ capability: root_cause_reasoning
             └─ agent: reasoning   ─────┐
                                        │
                                        ▼
                        图的节点顺序（固定）：
                        normalize_alarm → collect_topology → collect_logs
                        → reason → propose → supervise
```

**路由决定"这次请求属于哪类业务"，图决定"这类业务按什么步骤执行"。**

- 路由的 `agent` 字段表示"这个意图的主执行者"——用于自描述与权限校验。
- 图按 `NODE_ORDER` 顺序执行全部节点——因为一次故障处置需要完整的证据链，
  而不是只跑一个 Agent。

**`intent` 会写进 `WorkflowState`**，因此"这次处置是哪类意图"是可查询的，
并且会作为标签进指标（`aio_agentos_workflow_started{intent=diagnose}`）。

---

## 五、`SkillRegistry`

[`skills/registry.py`](../skills/registry.py) 是一个轻量容器：

```python
registry = build_default_skill_registry()
registry.skills          # dict[str, SkillSpec]
registry.capabilities    # dict[str, CapabilitySpec]
registry.for_intent("诊断")   # list[SkillSpec]
```

`build_default_skill_registry()` 在 [`skills/catalog.py`](../skills/catalog.py) 里装配：

```python
def build_default_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for capability in CAPABILITIES:
        registry.register_capability(capability)
    for skill in SKILLS:
        registry.register_skill(skill)
    return registry
```

由 [`bootstrap.py`](../bootstrap.py) 注入到 `Runtime.skills`，并被三处消费：

| 消费方 | 用途 |
|---|---|
| `RouteSkillRouter` | 意图路由 |
| `build_agent_card()` | 生成 A2A AgentCard 的 `skills` 字段 |
| `GET /skills` | 自描述端点 |

---

## 六、为什么这个模块曾经是孤立的

早期版本的 `skills/registry.py` 只提供了 `SkillRegistry` 这个**空容器**，
没有任何注册内容——于是 `router/` 整条链路**运行时零引用**：
代码写好了，但没有任何地方会调用它。

修复方式就是 [`skills/catalog.py`](../skills/catalog.py)：把领域目录补齐，
并在 `bootstrap.py` 里真实装配。

现在 [`tests/test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py) 有断言防止回归：

| 断言 | 防止什么 |
|---|---|
| 每个 Skill 的 `allowed_agents` 都能解析到真实 Agent | 路由指向不存在的执行者 |
| 每个 Skill 的 `required_capability` 都有 Agent 提供 | 能力声明悬空 |
| `router.intents()` 非空且覆盖 `DEFAULT_INTENT` | 目录被清空 |
| `router.plan()` 返回完整链路 | 流水线定义断裂 |

---

## 七、新增一个技能

假设要加一个"变更回滚建议"技能：

```python
# 1. 在 skills/catalog.py 声明 Capability
CapabilitySpec(name="change_rollback", description="分析变更历史并建议回滚点")

# 2. 声明 Skill
SkillSpec(
    name="suggest_rollback",
    version="1.0.0",
    description="变更回滚建议：基于最近变更历史给出回滚目标版本",
    intents=["rollback", "change", "回滚", "变更"],
    required_capability="change_rollback",
    allowed_agents=["rollback"],          # ← 必须有一个 Agent 提供该能力
    allowed_tools=["deploy.history"],     # ← 工具必须已注册
    risk_level=RiskLevel.LOW,
)

# 3. 在 tools/catalog.py 注册 deploy.history 工具规格
# 4. 在 agents/implementations.py 加 RollbackAgent，capabilities=["change_rollback"]
# 5. 在 agents/implementations.py 的 build_agents() 里注册
```

**不需要改路由代码、不需要改图代码**——新增技能的落点是声明，不是逻辑。

如果第 4 步漏了，`tests/test_router_skills_ontology.py` 的孤立组件检查会失败。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_router.py`](../tests/test_router.py) | 1 | 路由基础行为 |
| [`tests/test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py) | 19 | 孤立组件检查、路由、低风险优先、默认回落、计划链路 |

```bash
pytest tests/test_router.py tests/test_router_skills_ontology.py -q
```

---

## 未交付

- **Skill 版本路由**：`SkillSpec.version` 字段存在，但路由不区分版本——同名 Skill 只能有一个生效。
- **Skill 组合与嵌套**：没有"一个 Skill 由多个子 Skill 组成"的表达能力。
- **动态能力发现**：Skill 目录是启动时静态装配的，没有运行时注册/注销。
- **意图分类器**：`intent` 由调用方显式传入，没有从自然语言告警文本自动推断意图的模型。
- **Skill 级评估指标**：`AgentSpec` 有 `evaluation_metrics`，`SkillSpec` 没有。
