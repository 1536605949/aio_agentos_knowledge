# LLM 能力层

> 代码位置：[`llm/`](../llm/) ｜ 相关：[`agents/base.py`](../agents/base.py) 的 `call_llm`

LLM 能力层要解决两个问题：

1. **换模型不改业务代码**——Agent 只依赖协议，不依赖任何 SDK。
2. **提示词是被治理的资产**——有版本号、可灰度、可回滚，且能在 Trace 里回溯"这次输出是哪版提示词产生的"。

---

## 一、协议优先：`LLMClient`

[`llm/base.py`](../llm/base.py) 定义的 `LLMClient` 是一个 `@runtime_checkable` 的 `Protocol`，只有两个属性与一个方法：

```python
@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

Agent 侧（[`agents/base.py`](../agents/base.py)）只做三件事：

```python
request = LLMRequest(messages=[...], metadata={"task": task})
response = await self.llm.complete(request)
payload = response.json_object()      # 失败时抛 LLMResponseFormatError
```

**没有任何一处 `if provider == "openai"`。** 这是"换模型不改业务代码"的机械保证。

### 数据契约

| 模型 | 关键字段 | 说明 |
|---|---|---|
| `LLMMessage` | `role: LLMRole`、`content` | 角色用 `StrEnum` 限定为 `system` / `user` / `assistant` |
| `LLMRequest` | `messages`、`temperature`、`max_tokens`、`expect_json`、`metadata` | `metadata["task"]` 用于确定性实现分派任务类型 |
| `LLMUsage` | `prompt_tokens` / `completion_tokens` / `total_tokens` / `cost_usd` | 用量统计来源 |
| `LLMResponse` | `text`、`provider`、`model`、`usage`、`latency_ms`、`attempts`、`fallback_used` | `json_object()` 做稳健解析 |

### 异常层级

```text
LLMError                    # 基类
├── LLMUnavailable          # 未配置凭据 / 依赖缺失 —— 可安全回退
└── LLMResponseFormatError  # 返回体无法解析为期望结构
```

`ResilientLLM` 只捕获 `LLMError`：**格式错误也会触发降级**，因为对上层而言"拿不到可用 JSON"和"调不通"是同一类失败。

---

## 二、稳健的 JSON 抽取

模型返回 JSON 时最常见的三种脏输出，[`extract_json_object()`](../llm/base.py) 按优先级依次尝试：

| 顺序 | 策略 | 覆盖场景 |
|---|---|---|
| 1 | 整体 `json.loads` | 模型规规矩矩只输出 JSON |
| 2 | 剥离 markdown 围栏后解析 | ` ```json ... ``` ` 包裹 |
| 3 | 截取**首个平衡花括号片段** | 前后夹带解释性文字 |

第三种用状态机实现（`_extract_balanced_braces`），会跟踪字符串状态与转义符，因此 `{"a": "}"}` 这类含右花括号的字符串不会被误截断。

`tests/test_llm.py` 对 5 种格式逐一断言，包括"围栏 + 前后文字 + 内部含花括号字符串"的复合场景。

---

## 三、提示词治理：`PromptRegistry`

[`llm/prompts.py`](../llm/prompts.py) 刻意不引入 Jinja2 之类的模板引擎——只做 `{variable}` 占位符替换，换来的是**零依赖 + 可审计**。

### 为什么提示词需要版本号

一次线上事故复盘时，最常问的问题是"模型为什么给出这个答案"。如果提示词没有版本标识，这个问题无法回答。本项目的做法：

```python
with trace.span("llm.complete", kind=SpanKind.LLM,
                prompt=system_prompt, prompt_version=system_template.version) as span:
    response = await self.llm.complete(request)
    span.attributes.update({"provider": ..., "total_tokens": ..., "fallback_used": ...})
```

因此 `GET /workflow/{id}/trace` 返回的每个 `llm.complete` Span 上都带 `prompt_version` 与 `provider`——
**"哪次输出由哪版提示词、哪个 provider 产生"是可查的**。

### 多版本与灰度

`PromptRegistry` 按 `name` 索引模板，同名可存多版本：

```python
registry.register(PromptTemplate(name="reasoning.system", version="1.1.0", template=...), activate=False)
registry.activate("reasoning.system", "1.1.0")     # 灰度切换
registry.active_version("reasoning.system")        # -> "1.1.0"
```

回滚就是再 `activate` 回旧版本，不需要改代码、不需要重新部署。

### 渲染校验

`PromptTemplate.model_post_init` 自动从模板里提取 `{var}` 占位符填充 `variables`。
`render()` 做双向校验：

- **缺变量** → `PromptRenderError("missing prompt variables ...")`
- **渲染后仍有未解析占位符** → `PromptRenderError("unresolved placeholders ...")`

第二条很关键：如果模板里写了 `{{...}}`（JSON Schema 示例里常见）但没转义，渲染会**显式报错**而不是把 `{` 泄漏进提示词。

### 默认模板清单

| 模板名 | 版本 | 用途 |
|---|---|---|
| `reasoning.system` | 1.0.0 | 根因推理系统提示词，注入 `{allowed_causes}` 白名单 |
| `reasoning.user` | 1.0.0 | 渲染告警 + 证据 + 拓扑为推理请求 |
| `remediation.system` | 1.0.0 | 动作规划系统提示词，注入 `{allowed_actions}` 白名单 |
| `remediation.user` | 1.0.0 | 渲染根因 + 置信度 + 证据摘要 |
| `alarm.system` | 1.0.0 | 告警归一化，注入 `{allowed_severities}` |
| `alarm.user` | 1.0.0 | 渲染原始告警 JSON |

**注意白名单不是硬编码在提示词里的**——`{allowed_causes}` 由 [`ontology/domain.py`](../ontology/domain.py) 的
`root_causes_for_prompt()` 渲染。这是"词表单一事实源"的落点之一（另两处见 [本体设计](ontology-design.md)）。

---

## 四、三个 provider

[`llm/factory.py`](../llm/factory.py) 按 `AIO_AGENTOS_LLM_PROVIDER` 选择实现：

| provider | 实现文件 | 依赖 | 说明 |
|---|---|---|---|
| `deterministic`（默认） | [`llm/deterministic.py`](../llm/deterministic.py) | 无 | 信号词典 + 规则分派，零凭据可跑 |
| `openai` / `deepseek` / `openai-compatible` | [`llm/openai_compatible.py`](../llm/openai_compatible.py) | `httpx`（extra `openai`） | 调 `/chat/completions`，5xx/429 指数退避 |
| `langchain` | [`llm/langchain_adapter.py`](../llm/langchain_adapter.py) | `langchain-openai`（extra `langchain`） | 用 ChatModel 作适配层，**提示词资产仍由本项目管理** |

### 确定性实现不是玩具

[`llm/deterministic.py`](../llm/deterministic.py) 用三张信号词典做规则匹配：

- `_ROOT_CAUSE_SIGNALS` —— `timeout` → `upstream_timeout`、`oom|memory` → `resource_exhaustion`、`refused|connection` → `database_dependency_failure` …
- `_SEVERITY_SIGNALS` —— 从告警文本推断严重级别
- `_ACTION_BY_CAUSE` —— 根因 → 建议动作

它按 `request.metadata["task"]` 分派（`reasoning` / `remediation` / `alarm`），返回结构与真实 provider 完全一致。

**它的价值在于：让整条链路在零外部依赖的前提下可端到端运行并可测试。** CI 里跑的就是它——
133 个用例中没有任何一个需要网络或 API Key。

---

## 五、降级包装：`ResilientLLM`

```text
Agent ──▶ ResilientLLM ──▶ primary (openai / langchain / deterministic)
                │
                └── LLMError ──▶ fallback (DeterministicLLM)，并标记 fallback_used=True
```

设计取舍：

- **构建期降级**：真实 provider 构造失败（缺 `httpx`、缺 key）→ 直接换成 `DeterministicLLM`，服务照常启动。
- **调用期降级**：主 provider 抛 `LLMError` → 切 fallback，`response.fallback_used = True`，**不向上抛**。

理由：AIOps 场景里"降级给出一个基于规则的判断"远好于"整个处置流程因为模型网关抖动而失败"。
但如果降级发生，必须**可观测**——否则团队永远不知道自己在用规则而不是模型。

### 用量统计

`ResilientLLM.stats()` 直接挂到 `GET /metrics` 的 `llm` 字段：

```json
{
  "primary_provider": "deterministic",
  "fallback_provider": "deterministic",
  "model": "deterministic-rule-v1",
  "calls": 3,
  "failures": 0,
  "fallback_used": 0,
  "fallback_rate": 0.0,
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "total_tokens": 0
}
```

`fallback_rate` 是需要告警的核心指标：**它持续大于 0 说明主 provider 正在系统性失败**，
而此时业务功能看起来完全正常——这正是最危险的状态。

---

## 六、LLM 调用点

当前全项目只有 **3 个** LLM 调用点，全部经 `BaseAgent.call_llm`：

| Agent | task | 提示词 | 用途 |
|---|---|---|---|
| `AlarmAgent` | `alarm` | `alarm.system` / `alarm.user` | 归一化 severity 与分类 |
| `ReasoningAgent` | `reasoning` | `reasoning.system` / `reasoning.user` | 根因 + 置信度 + 理由 |
| `RemediationAgent` | `remediation` | `remediation.system` / `remediation.user` | 修复动作 + 风险说明 |

其余 3 个 Agent（`TopologyAgent` / `LogAgent` / `TicketAgent`）**不调用 LLM**——
它们做的是工具调用与结构转换，让模型参与只会增加不确定性。

**这是一条刻意的边界：LLM 只在"需要语义判断"的地方出现，其余路径保持确定性。**

---

## 七、如何替换为自己的模型

```python
# 1) 环境变量方式（推荐）
AIO_AGENTOS_LLM_PROVIDER=openai
AIO_AGENTOS_LLM_API_KEY=sk-xxx
AIO_AGENTOS_LLM_BASE_URL=https://your-gateway/v1
AIO_AGENTOS_LLM_MODEL=your-model-name

# 2) 代码方式：实现协议即可，无需继承任何基类
class MyLLM:
    provider = "internal-gateway"
    model = "my-model"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...   # 返回 LLMResponse 即可
```

只要满足 `LLMClient` 协议，`ResilientLLM`、Agent、工作流都不需要改动。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_llm.py`](../tests/test_llm.py) | 16 | JSON 抽取 5 种格式、提示词版本与渲染校验、确定性实现分派、降级统计 |

```bash
pytest tests/test_llm.py -q
```

---

## 未交付

- **流式输出（streaming）**：`LLMClient` 协议只定义了 `complete()`，没有 `stream()`。AIOps 处置链路是短请求-响应，流式的收益有限。
- **真实 token 计费**：`LLMUsage.cost_usd` 字段存在但恒为 0——本项目不内置任何厂商的价格表。
- **提示词自动优化**：BadCase 会被采集，但"根据 BadCase 自动改写提示词"没有实现，这是刻意留给人工的环节。
