# Agent 设计

> 代码位置：[`agents/`](../agents/) ｜ 基类：[`agents/base.py`](../agents/base.py)
> 实现：[`agents/implementations.py`](../agents/implementations.py) ｜ 注册表：[`agents/registry.py`](../agents/registry.py)

---

## 一、三类职责被刻意分开

Agent 是最容易变成"什么都往里塞"的组件。本项目把它的职责切成三块，每块有独立实现：

| 职责 | 实现 | 说明 |
|---|---|---|
| **生命周期** | [`runtime/lifecycle.py`](../runtime/lifecycle.py) | 显式状态机，非法迁移直接抛错 |
| **可观测** | [`observability/trace.py`](../observability/trace.py) | 每次调用是一个带层级的 Span |
| **LLM 交互** | [`agents/base.py`](../agents/base.py) 的 `call_llm` | 提示词从注册表取、变量由本体词表渲染、返回体解析为 JSON |

### 生命周期状态机

```python
class AgentLifecycle(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"     # 终态
    FAILED = "failed"           # 终态

_ALLOWED = {
    CREATED:   {RUNNING, FAILED},
    RUNNING:   {WAITING, COMPLETED, FAILED},
    WAITING:   {RUNNING, FAILED},
    COMPLETED: set(),           # 不可再迁移
    FAILED:    set(),
}

def transition(current, target):
    if target not in _ALLOWED[current]:
        raise ValueError(f"invalid lifecycle transition: {current.value} -> {target.value}")
    return target
```

`run()` 里迁移是显式的：

```python
context.lifecycle = transition(context.lifecycle, AgentLifecycle.RUNNING)
try:
    output = await self.execute(context, trace)
    self.validate_output(output)               # ← 输出契约校验
    context.outputs = output
    context.lifecycle = transition(context.lifecycle, AgentLifecycle.COMPLETED)
    return output
except Exception:
    context.lifecycle = transition(context.lifecycle, AgentLifecycle.FAILED)
    raise
```

**`COMPLETED` / `FAILED` 是终态**——避免了"一个已完成的 Agent 被再次推进到 RUNNING"这类诡异状态。

---

## 二、依赖全部通过构造函数注入

```python
class BaseAgent(ABC):
    spec: AgentSpec

    def __init__(
        self,
        llm: LLMClient | None = None,
        prompts: PromptRegistry | None = None,
        ontology: OntologyRegistry | None = None,
        tools: ToolRegistry | None = None,
        principal: Principal | None = None,
    ) -> None:
```

| 依赖 | 作用 | 测试中可替换为 |
|---|---|---|
| `llm` | LLM 调用 | `DeterministicLLM` / 假实现 |
| `prompts` | 提示词注册表 | 自定义版本的注册表 |
| `ontology` | 领域词表 | 改过词表的本体 |
| `tools` | 受治理工具注册表 | 只有部分工具的注册表 |
| `principal` | 调用主体身份 | 不同角色的 Principal |

所有参数都有默认值（便于单测），但**生产路径上全部由 [`bootstrap.py`](../bootstrap.py) 注入同一批对象**：

```python
AgentRegistry(build_agents(llm=llm, prompts=prompts, ontology=ontology, tools=tools))
```

因此测试里可以替换任意一层而不改 Agent 代码。

---

## 三、`AgentSpec`：七项声明

```python
class AgentSpec(BaseModel):
    name: str
    capabilities: list[str]              # 1. Capability
    skills: list[str]                    # 2. Skill
    input_schema: dict                   # 3. Input Schema
    output_schema: dict                  # 4. Output Schema
    tool_permissions: list[str]          # 5. Tool Permission
    memory_policy: MemoryPolicy          # 6. Memory Policy
    evaluation_metrics: list[EvaluationMetric]   # 7. Evaluation Metric
```

`agents/implementations.py` 的 `_spec()` 帮助函数把七项一次声明完：

```python
def _spec(name, capability, skill, tools, output_properties) -> AgentSpec:
    return AgentSpec(
        name=name,
        capabilities=[capability],
        skills=[skill],
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": output_properties},
        tool_permissions=tools,
        memory_policy=MemoryPolicy(read_types=["short", "episodic"], write_types=["short"]),
        evaluation_metrics=[
            EvaluationMetric(name="schema_validity", target=1.0),
            EvaluationMetric(name="ontology_conformance", target=1.0),
        ],
    )
```

### `output_schema` 是可执行的

`validate_output()` 按 `output_schema.properties` 校验必需字段：

```python
def validate_output(self, output: dict[str, Any]) -> None:
    properties = (self.spec.output_schema or {}).get("properties") or {}
    missing = [name for name in properties if name not in output]
    if missing:
        raise AgentOutputError(f"{self.spec.name} output missing declared fields: {missing}")
```

这是**最轻量的契约检查**——不引入 JSON Schema 库，但保证声明的字段一定出现在输出里。
（完整 JSON Schema 校验属于部署环节的额外加固。）

---

## 四、六个 Agent

```text
                   ┌─────────────────────────────────────────┐
   告警输入 ────────▶│ AlarmAgent      normalize_alarm        │  证据标准化
                   │  （LLM 调用 ①）                          │
                   ├─────────────────────────────────────────┤
                   │ TopologyAgent   collect_topology       │  拓扑取证
                   │  （工具 topology.lookup）                │
                   ├─────────────────────────────────────────┤
                   │ LogAgent        collect_logs           │  日志取证
                   │  （工具 logs.search）                    │
                   ├─────────────────────────────────────────┤
                   │ ReasoningAgent  infer_root_cause       │  根因推理
                   │  （LLM 调用 ②，受词表约束）               │
                   ├─────────────────────────────────────────┤
                   │ RemediationAgent remediate_incident    │  动作规划
                   │  （LLM 调用 ③，审批门来自工具风险）        │
                   ├─────────────────────────────────────────┤
                   │ TicketAgent     create_ticket          │  工单升级
                   │  （工具 ticket.create）                  │
                   └─────────────────────────────────────────┘
```

### 逐个说明

| Agent | capability | skill | tool_permissions | 是否调 LLM |
|---|---|---|---|---|
| `AlarmAgent` | `alarm_normalization` | `normalize_alarm` | — | ✅ `alarm.*` |
| `TopologyAgent` | `topology_evidence` | `collect_topology` | `topology.lookup` | ❌ |
| `LogAgent` | `log_evidence` | `collect_logs` | `logs.search` | ❌ |
| `ReasoningAgent` | `root_cause_reasoning` | `infer_root_cause` | — | ✅ `reasoning.*` |
| `RemediationAgent` | `remediation` | `remediate_incident` | `service.restart` | ✅ `remediation.*` |
| `TicketAgent` | `ticketing` | `create_ticket` | `ticket.create` | ❌ |

**只有 3 个 Agent 调用 LLM。** 拓扑/日志/工单这三个做的是工具调用与结构转换，
让模型参与只会增加不确定性。这是一条刻意的边界：
**LLM 只在"需要语义判断"的地方出现。**

### 各 Agent 的关键设计

**`AlarmAgent`** —— 归一化后做**词表兜底**：

```python
severity = str(payload.get("severity", "unknown")).lower()
if severity not in SEVERITIES:
    severity = "unknown"
category = str(payload.get("category", "unclassified"))
if category not in ALARM_CATEGORIES:
    category = "unclassified"
```

并在返回体里记录 `normalized_by: response.provider`——**哪个 provider 做的归一化是可查的**。

**`TopologyAgent`** —— 优先用调用方传入的 `topology`，缺省时才调工具：

```python
topology = context.inputs.get("topology")
if not topology and self.tools is not None and self.tools.has("topology.lookup"):
    ...
return {"topology": topology or {"dependencies": []}}
```

这样离线测试可以不依赖工具。

**`LogAgent`** —— 把日志行结构化为带信号标记的证据：

```python
_ERROR_HINTS = ("error", "timeout", "oom", "refused", "fail", "exception", "panic", "unreachable")

evidence = [
    {"message": str(line),
     "signal": "error" if any(hint in str(line).lower() for hint in self._ERROR_HINTS) else "info"}
    for line in logs
]
```

**这是纯规则、无 LLM**——日志信号识别用关键词足够，且可解释、零成本。

**`ReasoningAgent`** —— 词表收敛 + 置信度压制：

```python
cause = raw_cause if is_valid_root_cause(raw_cause) else "undetermined"
confidence = _clamp(payload.get("confidence", 0.0))
if cause == "undetermined":
    confidence = min(confidence, 0.3)      # 证据不足时置信度不得超过 0.3
```

返回体里同时保留 `raw_root_cause`（模型原话）与 `ontology_violation`（是否越界）。

**`RemediationAgent`** —— 审批门**由工具风险推导**：

```python
def _requires_approval(self, tool: str | None, action: str) -> bool:
    """审批门来自工具规格声明的风险等级——单一事实源。"""
    if tool is None:
        return False
    if self.tools is not None and self.tools.has(tool):
        return int(self.tools.get(tool).risk_level) >= _HIGH_RISK
    return int(ACTION_RISK.get(action, 0)) >= _HIGH_RISK
```

早期实现写的是 `action == "restart_service"` 这类启发式，新增高风险动作时容易被漏掉。
现在新增动作只需在 `ACTION_TO_TOOL` 与工具规格里声明一次。

注意 `tool is None` 时返回 `False`：`collect_more_evidence` **刻意没有工具映射**，
因此它天然不需要审批。

**`TicketAgent`** —— 工单内容来自 `context.inputs`，`workflow_id` 直接取自上下文：

```python
payload = {
    "title": str(inputs.get("title") or f"AIOps incident: {inputs.get('root_cause', 'unknown')}"),
    "service": ..., "severity": ..., "root_cause": ...,
    "workflow_id": context.workflow_id,
}
```

没有工具注册表时返回 `{"ticket": {**payload, "status": "prepared"}}`——
**降级路径明确，不静默失败**。

---

## 五、`AgentContext` 的生命周期

```python
class AgentContext(BaseModel):
    """Ephemeral per-agent-call state. It is never the durable workflow source of truth."""

    workflow_id: str
    trace_id: str
    agent_name: str
    lifecycle: AgentLifecycle = AgentLifecycle.CREATED
    inputs: dict[str, Any] = Field(default_factory=dict)
    memory_refs: list[str] = Field(default_factory=list)
    permissions: set[str] = Field(default_factory=set)
    outputs: dict[str, Any] = Field(default_factory=dict)
```

**每次 Agent 调用都新建一个**——[`graph/nodes.py`](../graph/nodes.py) 的 `GraphDeps.context()`：

```python
def context(self, agent_name: str, inputs: dict[str, Any]) -> AgentContext:
    return AgentContext(
        workflow_id=self.workflow_id,
        trace_id=self.trace.trace_id,
        agent_name=agent_name,
        inputs=inputs,
    )
```

**绝不跨 Agent 调用复用**——这是 [状态所有权](state-ownership.md) 的第二条红线。
原因见该文档的故障推演。

---

## 六、两个调用助手

### `call_llm` —— 渲染 → 调用 → 解析

```python
system_template = self.prompts.get(system_prompt)
user_template = self.prompts.get(user_prompt)
request = LLMRequest(
    messages=[
        LLMMessage(role=LLMRole.SYSTEM, content=system_template.render(**variables)),
        LLMMessage(role=LLMRole.USER, content=user_template.render(**variables)),
    ],
    metadata={"task": task, **(metadata or {})},
)
with trace.span("llm.complete", kind=SpanKind.LLM,
                task=task, prompt=system_prompt,
                prompt_version=system_template.version) as span:
    response = await self.llm.complete(request)
    span.attributes.update({
        "provider": response.provider,
        "model": response.model,
        "total_tokens": response.usage.total_tokens,
        "fallback_used": response.fallback_used,
        "latency_ms": response.latency_ms,
    })
return response.json_object(), response
```

返回 `(payload, response)` 二元组——Agent 既能拿到解析后的数据，也能读到 provider 元信息
（例如 `AlarmAgent` 记录 `normalized_by`）。

**`prompt_version` 记录在 Span 上**，这是"这次输出是哪版提示词产生的"可回溯的机械保证。

### `call_tool` —— 走受治理路径

```python
async def call_tool(self, name, payload, trace) -> dict[str, Any]:
    if self.tools is None or not self.tools.has(name):
        raise AgentOutputError(f"tool not available to agent {self.spec.name}: {name}")
    return await self.tools.invoke(name, payload, principal=self.principal, trace=trace)
```

**Agent 没有绕过治理层的路径**——它只能通过 `ToolRegistry.invoke` 触发副作用，
而 `invoke` 内部会做策略/权限/限流/幂等/熔断检查。`principal` 来自构造注入，
不是请求参数——**Agent 无法自报身份**。

---

## 七、`AgentRegistry`：让规格成为运行时事实

```python
class AgentRegistry:
    def names(self) -> list[str]: ...
    def specs(self) -> list[AgentSpec]: ...
    def capabilities(self) -> dict[str, str]:      # capability -> agent 名
    def tools_for(self, name: str) -> list[str]: ...
    def describe(self) -> list[dict[str, Any]]: ...
```

`capabilities()` 有一个具体用途：**校验 Skill 声明的 capability 是否有承接者**。
如果某个 Skill 的 `required_capability` 没有 Agent 提供，路由就会指向一个不存在的执行者。

`describe()` 直接喂给 `GET /agents` 端点，也喂给 `build_agent_card()` 生成 A2A AgentCard。

---

## 八、孤立组件检查

早期版本的 `router/` + `skills/`、`ontology/`、`protocol/`、`graph/langgraph_adapter.py`
代码完整但**运行时零引用**——写好了却没人调。

现在 [`tests/test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py) 有一组
**孤立组件检查**用例，断言：

| 断言 | 防止什么 |
|---|---|
| 6 个 Agent 全部在 `AgentRegistry` 中 | 写了 Agent 但没注册 |
| 每个 Skill 的 `allowed_agents` 都能解析到真实 Agent | 路由指向不存在的执行者 |
| 每个 Skill 的 `required_capability` 都有 Agent 提供 | 能力声明悬空 |
| 每个 `ACTION_TO_TOOL` 的目标工具都已注册 | 动作映射到不存在的工具 |
| 本体词表三处消费结果一致 | 词表被改但消费点没跟上 |

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_agents.py`](../tests/test_agents.py) | 1 | Agent 基础行为 |
| [`tests/test_router_skills_ontology.py`](../tests/test_router_skills_ontology.py) | 19 | 孤立组件检查、词表收敛、审批门、Agent 注册 |

```bash
pytest tests/test_agents.py tests/test_router_skills_ontology.py -q
```

---

## 未交付

- **Agent 之间的显式协商（A2A 消息）**：Agent 由图的节点顺序驱动，没有实现 Agent 间消息传递协议。
  `protocol/a2a/models.py` 只提供 `AgentCard`（能力发现），没有 `Task` / `Message` 语义。
- **动态 Agent 选择**：执行者由 Skill 的 `allowed_agents[0]` 静态决定，没有基于负载/成本的调度。
- **Agent 级并行**：`collect_topology` 与 `collect_logs` 语义上可并行，当前是顺序执行。
- **Agent 级独立超时**：只有整体图超时（`AIO_AGENTOS_REASONING_TIMEOUT_SECONDS`）。
- **完整 JSON Schema 校验**：`validate_output` 只检查字段是否存在，不做类型/约束校验。
- **Agent 自主规划（plan-and-execute）**：图拓扑是静态的，没有让模型决定下一步的机制。
