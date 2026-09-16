# 本体设计

> 代码位置：[`ontology/`](../ontology/) ｜ 领域词表：[`ontology/domain.py`](../ontology/domain.py)
> 消费方：[`graph/nodes.py`](../graph/nodes.py) 的 `supervise`、[`agents/implementations.py`](../agents/implementations.py)

「本体驱动」这个词很容易沦为文档描述。本项目把它落到一个**可执行、可测试**的机制上：

> **领域词表在本体里唯一定义一次，然后被三处共同消费。**
> 因此「LLM 只能在业务边界内生成内容」不是一句承诺，而是一条有落点的约束链。

---

## 一、词表单一事实源

[`ontology/domain.py`](../ontology/domain.py) 里的三个元组是**全项目唯一的定义点**：

```python
ROOT_CAUSES = (
    "upstream_timeout", "resource_exhaustion", "database_dependency_failure",
    "config_regression", "network_partition", "undetermined",
)
SEVERITIES = ("critical", "high", "medium", "low", "unknown")
REMEDIATION_ACTIONS = (
    "restart_service", "rollback_config", "scale_out",
    "escalate_to_dba", "escalate_to_network_team", "collect_more_evidence",
)
ALARM_CATEGORIES = (*ROOT_CAUSES, "unclassified")
```

注意 `undetermined` 与 `collect_more_evidence` 的存在是刻意的：

- `undetermined` 是**显式的"证据不足"出口**，避免模型在无证据时被迫编造一个具体根因。
- `collect_more_evidence` 是**无副作用的动作出口**，且它**刻意没有映射到任何工具**
  （见 [`tools/catalog.py`](../tools/catalog.py) 的 `ACTION_TO_TOOL`）。

### 三处消费点

| # | 消费点 | 代码 | 作用 |
|---|---|---|---|
| 1 | **Prompt 渲染** | `root_causes_for_prompt()` 注入 `{allowed_causes}` | 从源头约束模型的输出空间 |
| 2 | **本体公理校验** | `supervise` 节点调 `ontology.validate()` | 在执行前拦截越界输出 |
| 3 | **Agent 输出校验** | `is_valid_root_cause()` / `is_valid_action()` | 越界值收敛到安全出口 |

```text
                   ┌──────────────────────────────┐
                   │  ontology/domain.py 词表      │  ← 唯一定义点
                   └──────────────┬───────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
  ① Prompt 渲染             ② 公理校验                 ③ 输出校验
  "root_cause 必须从         ALLOWED_VALUE 公理         越界 → undetermined
   允许列表中取值:          在 supervise 节点拦截       标记 ontology_violation
   upstream_timeout, ..."   （越界即失败）
```

**三者任一单独存在都不够**：只有 Prompt 约束，模型仍可能越界；只有输出校验，
模型会浪费 token 在无效取值上；只有公理校验，则错误发现得太晚（已经跑完推理）。
三处叠加才构成完整约束链。

### 收敛而非报错

Agent 层的处理是**收敛**而不是直接抛错：

```python
raw_cause = str(payload.get("root_cause", "undetermined"))
cause = raw_cause if is_valid_root_cause(raw_cause) else "undetermined"
if cause == "undetermined":
    confidence = min(confidence, 0.3)        # 证据不足时压低置信度
return {
    "root_cause": cause,
    "raw_root_cause": raw_cause,             # 保留原始值供复盘
    "ontology_violation": raw_cause != cause,
    ...
}
```

`raw_root_cause` 与 `ontology_violation` 保留下来，用于：

- **BadCase 归档**（`BadCaseCategory.ONTOLOGY_VIOLATION`）
- **提示词迭代**——越界取值告诉我们模型"想说什么"，这是改进词表或提示词的直接输入

---

## 二、TBox 结构

[`ontology/models.py`](../ontology/models.py) 定义只读 TBox，五个构件：

| 构件 | 类 | 数量 | 说明 |
|---|---|---|---|
| 类 | `ClassDef` | 10 | `Incident` / `Alarm` / `Service` / `Host` / `Evidence` / `RootCause` / `RemediationAction` / `Agent` / `Skill` / `Tool` |
| 属性 | `PropertyDef` | 8 | 带 `domain` / `range` / `required` |
| 关系 | `RelationDef` | 6 | `CAUSED_BY` / `RESOLVED_BY` / `HAS_CAPABILITY` / `USES_TOOL` / `LOCATED_ON` / `DEPENDS_ON` |
| 公理 | `OntologyAxiom` | 4 | 见下节 |
| 版本 | `OntologyVersion` | — | `MAJOR.MINOR.PATCH` + `compatible_from` |

### 为什么运行实例不入本体

`OntologyRegistry` 的文档字符串写得很明确：

> Read-only TBox registry. Runtime/ABox instances deliberately live elsewhere.

告警、设备、执行记录属于 **ABox**（断言层），它们的生命周期是"一次故障"，
而 TBox 的生命周期是"一个本体版本"。混在一起会导致：

- 本体表被高频写入，版本语义失效
- 无法回答"这条根因判断用的是哪版词表"

因此运行实例落在 `WorkflowState` 与 `DocumentStore`（见 [状态所有权](state-ownership.md)）。

---

## 三、四类公理

```python
class AxiomKind(StrEnum):
    REQUIRED_FIELD = "required_field"
    ALLOWED_VALUE = "allowed_value"
    HIGH_RISK_REQUIRES_APPROVAL = "high_risk_requires_approval"
```

### 已声明的四条公理

| 名称 | 类型 | 作用 |
|---|---|---|
| `incident_id_required` | `REQUIRED_FIELD` | `workflow_id` 必须存在 |
| `root_cause_in_vocabulary` | `ALLOWED_VALUE` | `root_cause` 必须在 `ROOT_CAUSES` 内 |
| `severity_in_vocabulary` | `ALLOWED_VALUE` | `severity` 必须在 `SEVERITIES` 内 |
| `high_risk_requires_approval` | `HIGH_RISK_REQUIRES_APPROVAL` | 高风险动作必须有 `approved=True` |

### `ALLOWED_VALUE` 的一个关键设计

```python
if self.kind == AxiomKind.ALLOWED_VALUE:
    # 字段缺席时不判定：公理约束的是"一旦出现必须落在词表内"，
    # 这样同一条公理既能校验推理计划，也能校验执行记录。
    if not self.field or context.get(self.field) is None:
        return True, None
    value = context[self.field]
    ok = value in self.allowed_values
    return ok, None if ok else f"{self.field}={value!r} is not allowed"
```

如果字段缺席就判失败，那么这条公理在推理阶段（`root_cause` 已生成）能过，
但在执行阶段（只关心 `action`）会误报。**缺席即跳过**让同一条公理可以在两个阶段复用。

### 公理校验的可执行落点

`supervise` 节点（[`graph/nodes.py`](../graph/nodes.py)）是唯一的校验点：

```python
with deps.trace.span("graph.supervise", kind=SpanKind.GOVERNANCE, ...) as span:
    errors = list(state.get("errors", []))
    errors.extend(deps.ontology.validate(dict(state)))
    if not state.get("workflow_id"):
        errors.append("workflow_id missing")
    if state.get("root_cause") is None:
        errors.append("root_cause missing")
    if state.get("proposed_action") is None:
        errors.append("proposed_action missing")
    missing_nodes = [name for name in NODE_ORDER[:-1] if name not in deps.steps]
    if missing_nodes:
        errors.append(f"graph did not execute nodes: {missing_nodes}")
    if len(deps.steps) > deps.max_steps:
        errors.append(f"graph exceeded max steps ({deps.max_steps})")
```

除公理外还校验三件事：**必需字段存在**、**图节点完整执行**、**未超步数上限**。
Span 的 `kind=GOVERNANCE`，因此治理检查在 Trace 树里是独立可辨的一层。

`errors` 非空 → 外层工作流直接失败，**不进入执行阶段**：

```python
if state.ontology_errors:
    self._capture(BadCaseCategory.ONTOLOGY_VIOLATION, "supervisor", ...)
    raise RuntimeError(f"ontology validation failed: {state.ontology_errors}")
```

---

## 四、版本与兼容性

```python
class OntologyVersion(BaseModel):
    version: str                            # 必须是 MAJOR.MINOR.PATCH
    compatible_from: str | None = None
    notes: str = ""

    @field_validator("version", "compatible_from")
    @classmethod
    def semantic_version(cls, value):
        if value is not None and not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("version must use MAJOR.MINOR.PATCH")
        return value
```

当前基线：`build_ontology(version="3.5.0", compatible_from="3.0.0")`。

兼容性判定：

```python
def is_compatible_with(self, other_version: str) -> bool:
    if self.version.compatible_from is None:
        return True
    return _semver_tuple(other_version) >= _semver_tuple(self.version.compatible_from)
```

| 变更类型 | 版本动作 |
|---|---|
| 新增可选类/属性/关系 | MINOR +1 |
| 文档/描述修正 | PATCH +1 |
| 删除或重命名词表取值、收紧 `allowed_values` | **MAJOR +1**，并更新 `compatible_from` |

**为什么需要 `compatible_from`**：历史工作流的 `WorkflowState` 里存着当时的 `root_cause` 取值。
如果词表收紧了，旧记录按新词表校验会"失败"，但它们**在当时是合法的**。
`compatible_from` 声明了"哪些版本的数据仍然可读"，避免把历史数据误判为脏数据。

---

## 五、关系（`RelationDef`）与它们表达什么

```python
RelationDef(name="CAUSED_BY",      source_class="Incident",          target_class="RootCause",         kind=RelationKind.CAUSED_BY)
RelationDef(name="RESOLVED_BY",    source_class="RootCause",         target_class="RemediationAction", kind=RelationKind.DEPENDS_ON)
RelationDef(name="HAS_CAPABILITY", source_class="Agent",             target_class="Skill",             kind=RelationKind.HAS_CAPABILITY)
RelationDef(name="USES_TOOL",      source_class="Skill",             target_class="Tool",              kind=RelationKind.USES_TOOL)
RelationDef(name="LOCATED_ON",     source_class="Service",           target_class="Host",              kind=RelationKind.LOCATED_ON)
RelationDef(name="DEPENDS_ON",     source_class="Service",           target_class="Service",           kind=RelationKind.DEPENDS_ON)
```

这六条关系不是装饰，它们**描述了系统的两个关键约束**：

| 关系 | 表达的约束 | 代码里的对应 |
|---|---|---|
| `CAUSED_BY` | 故障必须归因到词表内的根因 | `ROOT_CAUSES` + `root_cause_in_vocabulary` 公理 |
| `RESOLVED_BY` | 根因对应标准处置动作 | `ACTION_TO_ROOT_CAUSE` 映射 |
| `HAS_CAPABILITY` | Agent 必须具备声明的能力 | `AgentSpec.capabilities` + `AgentRegistry.capabilities()` |
| `USES_TOOL` | Skill 是工具调用的**权限边界** | `SkillSpec.allowed_tools` + 治理层复核 |
| `DEPENDS_ON` | 服务间依赖用于拓扑推断 | `topology.lookup` 工具 |

**`USES_TOOL` 尤其重要**：它让"Agent 能用哪些工具"成为**可审计的静态声明**，
而不是散落在代码里的隐式调用。`remediate_incident` Skill 声明了
`allowed_tools=["service.restart", "config.rollback", "service.scale_out"]`，
这既是文档，也是可被治理层复核的约束。

---

## 六、测试

[`tests/test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py)（19 用例）与
[`tests/test_ontology.py`](../tests/test_ontology.py)（2 用例）中的本体相关部分：

| 断言 | 验证什么 |
|---|---|
| 词表被三处一致消费 | 改 `ROOT_CAUSES` 后 Prompt、公理、输出校验同步变化 |
| 越界根因被收敛到 `undetermined` | Agent 层收敛逻辑 |
| 越界动作被收敛到 `collect_more_evidence` | 同上 |
| `ALLOWED_VALUE` 在字段缺席时不报错 | 公理可跨阶段复用 |
| 本体错误导致工作流失败 | `supervise` → `_run` 的失败路径 |
| `is_compatible_with` 语义 | 版本兼容判定 |

```bash
pytest tests/test_router_skills_ontology.py tests/test_ontology.py -q
```

---

## 未交付

- **推理机（reasoner）**：本体没有实现描述逻辑推理，公理是**手写的三类检查器**，不是通用推理引擎。
- **TBox 持久化与热更新**：本体在 `bootstrap.build_runtime()` 里构建，改词表需要重新部署。
- **ABox 存储与 SPARQL 查询**：没有三元组存储。
- **本体可视化**：`/ontology` 返回 JSON，没有图形化展示。
- **自动一致性检查**：没有校验"公理集合自身是否矛盾"的机制。
