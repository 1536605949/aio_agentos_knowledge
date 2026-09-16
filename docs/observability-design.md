# 可观测性与闭环

> 代码位置：[`observability/`](../observability/)
> Trace：[`observability/trace.py`](../observability/trace.py) ｜ 指标：[`observability/metrics.py`](../observability/metrics.py)
> BadCase：[`observability/badcase.py`](../observability/badcase.py)

> **一句话结论**：Trace 是真正的父子树（用 `contextvars` 维护 Span 栈），异常路径也落盘，
> 且跨重启可回读；BadCase 有 10 类真实生产者，构成可查询的闭环入口。

---

## 一、Trace：为什么必须是树

### 早期实现的问题

早期版本的每个 Span 的 `parent_span_id` **恒为 `None`**——所有 Span 都是兄弟节点。
后果：

```text
调用树（错误）
- workflow.run
- graph.run
- agent.run        ← 哪个 agent？
- llm.complete     ← 属于哪个 agent？
- agent.run
- tool.invoke      ← 属于哪个 agent？
```

**扁平 Trace 无法回答最基本的问题："这个 LLM 调用是哪个 Agent 发起的？"**

### 修复：`contextvars` 维护当前 Span 栈

```python
_current_span: ContextVar[Span | None] = ContextVar("aio_current_span", default=None)

@contextmanager
def span(self, name, parent_span_id=None, kind=SpanKind.AGENT, **attributes) -> Iterator[Span]:
    parent = parent_span_id
    if parent is None:
        current = _current_span.get()
        parent = current.span_id if current is not None else None

    span = Span(trace_id=self.trace_id, parent_span_id=parent,
                name=name, kind=SpanKind(kind), attributes=attributes)
    token = _current_span.set(span)
    started = perf_counter()
    try:
        yield span
    except Exception as exc:
        span.status = SpanStatus.ERROR
        span.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        span.ended_at = datetime.now(UTC)
        span.duration_ms = round((perf_counter() - started) * 1000, 3)
        with self._lock:
            self._spans.append(span)
        _current_span.reset(token)        # ← 栈复位
```

**父 Span 判定优先级**：显式传入的 `parent_span_id` > 当前上下文栈顶 > 无父节点。

因此嵌套的 `with trace.span(...)` **自动建立父子关系**，调用方不需要手动传 parent id。

### 为什么用 `ContextVar` 而不是全局变量

`ContextVar` 在 `asyncio` 任务之间是**隔离的**。`test_trace_is_safe_under_concurrent_agents`
并发跑 8 个 Agent，断言得到 8 个根 Span——如果用全局变量，Span 会互相串台。

### `_current_span.reset(token)` 必须执行

`test_span_stack_is_reset_after_exit` 专门验证这一点：

```python
trace = AgentTrace()
with trace.span("first"):
    pass
with trace.span("second"):
    pass
assert trace.summary()["root_span_count"] == 2      # 两个独立根，而不是嵌套
```

如果忘了 `reset`，第二个 Span 会挂到第一个下面——**这是一个很容易犯且很难发现的错误**。

### 异常路径也落盘

`finally` 里先标记 `ERROR` 再写入：

```python
except Exception as exc:
    span.status = SpanStatus.ERROR
    span.error = f"{type(exc).__name__}: {exc}"
    raise
finally:
    ...
    with self._lock:
        self._spans.append(span)
```

`test_failed_span_is_recorded_on_exception` 断言：抛异常后 `failed_spans()` 有一条，
`status == "error"`，`error` 含 `"RuntimeError: boom"`，且 `span_count == 1`。

**这是 Agent 系统排障最关键的部分**——如果只有成功路径落盘，
出问题时你看到的是一棵"执行到一半就断掉"的树，不知道哪里炸了。

---

## 二、`SpanKind`：七类着色分类

```python
class SpanKind(StrEnum):
    WORKFLOW = "workflow"        # 外层持久化工作流
    ACTIVITY = "activity"        # 一次 Activity（修复执行）
    AGENT = "agent"              # 一次 Agent 调用
    GRAPH_NODE = "graph_node"    # 内层推理图整体
    TOOL = "tool"                # 一次受治理工具调用
    LLM = "llm"                  # 一次 LLM 补全
    GOVERNANCE = "governance"    # 治理校验（本体公理）
```

`kind` 让前端可以做差异化渲染（折叠、着色、过滤）。**`GOVERNANCE` 尤其有用**——
治理检查在 Trace 树里是独立可辨的一层，而不是混在业务节点里。

---

## 三、真实调用树

`aio-agentos demo` 的实际输出：

```text
- [workflow] workflow.run (5.8ms)
  - [graph_node] graph.run (5.6ms)
    - [agent] agent.run (1.6ms)              ← alarm
      - [llm] llm.complete (1.0ms)
    - [agent] agent.run (1.6ms)              ← topology
      - [tool] tool.invoke (0.1ms)
    - [agent] agent.run (0.0ms)              ← log
    - [agent] agent.run (0.9ms)              ← reasoning
      - [llm] llm.complete (0.3ms)
    - [agent] agent.run (0.4ms)              ← remediation
      - [llm] llm.complete (0.1ms)
    - [governance] graph.supervise (0.0ms)
- [activity] workflow.remediation (0.4ms)
  - [tool] tool.invoke (0.1ms)
```

`summary()` 的实测值：

```json
{
  "span_count": 14,
  "root_span_count": 2,
  "error_span_count": 0,
  "max_depth": 4,
  "total_duration_ms": 6.2,
  "by_name": {"agent.run": 5, "graph.run": 1, "graph.supervise": 1, "llm.complete": 3,
              "tool.invoke": 2, "workflow.remediation": 1, "workflow.run": 1}
}
```

**`root_span_count: 2` 值得解释**：`workflow.run` 覆盖推理阶段，`workflow.remediation`
覆盖审批后的执行阶段。两者都是根 Span——因为推理阶段在 `WAITING_APPROVAL` 时**已经返回**，
审批与执行发生在那之后，不在 `workflow.run` 的上下文里。

这恰好说明 Span 栈的语义是准确的：**它反映真实的调用嵌套，而不是为了让树好看而强行套一层**。

**`max_depth: 4` 是 `aio-agentos check` 的断言之一**——`< 2` 即判定"Trace 是扁平的"。

### `llm.complete` Span 的属性

```json
{
  "name": "llm.complete",
  "kind": "llm",
  "attributes": {
    "task": "reasoning",
    "prompt": "reasoning.system",
    "prompt_version": "1.0.0",
    "provider": "deterministic",
    "model": "deterministic-rule-v1",
    "total_tokens": 0,
    "fallback_used": false,
    "latency_ms": 0.1
  }
}
```

**`prompt_version` + `provider` 是可回溯性的机械保证**：
"这次输出是哪版提示词、哪个 provider 产生的"不需要靠猜。

---

## 四、读取视图

```python
def spans(self) -> list[Span]                 # 全部 Span（扁平）
def roots(self) -> list[Span]                 # 根 Span
def children_of(self, span_id) -> list[Span]  # 直接子节点
def failed_spans(self) -> list[Span]          # status == ERROR
def tree(self) -> list[dict]                  # 嵌套结构（前端直接渲染）
def summary(self) -> dict                     # 计数 + 最大深度 + 按名聚合
def to_dict(self) -> dict                     # 完整序列化（归档用）
```

`tree()` 的实现是先按 `parent_span_id` 分组再递归：

```python
by_parent: dict[str | None, list[Span]] = {}
for span in snapshot:
    by_parent.setdefault(span.parent_span_id, []).append(span)

def build(parent_id):
    return [{**span.model_dump(mode="json"), "children": build(span.span_id)}
            for span in by_parent.get(parent_id, [])]
return build(None)
```

### 跨重启回读

```python
@classmethod
def from_dict(cls, payload: dict[str, Any]) -> AgentTrace:
    spans = [Span.model_validate(item) for item in payload.get("spans", [])]
    trace_id = payload.get("trace_id") or str(uuid4())
    return cls.from_spans(trace_id, spans)
```

`parent_span_id` 被完整保留，因此**重启后重建的调用树结构与原来一致**。
归档由 `temporal/workflow.py::_persist()` 完成（写 `traces` collection）。

---

## 五、指标

[`observability/metrics.py`](../observability/metrics.py) 是进程内累加，零外部依赖。

```python
class MetricsCollector:
    def increment(self, name, value=1.0, **labels) -> None   # 计数器
    def gauge(self, name, value, **labels) -> None           # 仪表
    def observe(self, name, value, **labels) -> None         # 时长分布
    def counter(self, name, **labels) -> float
    def percentile(self, name, quantile, **labels) -> float
    def snapshot(self) -> dict
    def samples(self) -> list[MetricSample]
    def reset(self) -> None
```

### 键名规范化

```python
PREFIX = "aio_agentos"

def _key(name: str, labels: dict[str, str]) -> str:
    full = name if name.startswith(PREFIX) else f"{PREFIX}_{name}"
    if not labels:
        return full
    suffix = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
    return f"{full}{{{suffix}}}"
```

因此 `increment("workflow_started", intent="diagnose")` 产生的键是：

```text
aio_agentos_workflow_started{intent=diagnose}
```

标签**排序**保证同一组标签永远产生同一个键（不会因为字典顺序不同而分裂成两个计数器）。

### 已埋点的指标

| 指标 | 类型 | 标签 | 埋点位置 |
|---|---|---|---|
| `workflow_started` | counter | `intent` | `IncidentWorkflowService.start` |
| `workflow_status` | counter | `status` | `_transition` |
| `workflow_finished` | counter | `status` | `_record_duration`（终态时） |
| `workflow_paused` | counter | `status` | `_record_duration`（挂起时） |
| `workflow_failed` | counter | — | `_fail` |
| `workflow_duration_ms` | observe | `intent` | `_record_duration` |
| `tool_invocations` | counter | `tool` | `ToolRegistry._record_latency` |
| `tool_latency_ms` | observe | `tool` | 同上 |

### `workflow_paused` 为什么单独计数

```python
if state.is_terminal:
    self.metrics.increment("workflow_finished", status=state.status.value)
else:
    # 挂起等待审批不算"结束"，单独计数，避免看板把暂停误读为完成
    self.metrics.increment("workflow_paused", status=state.status.value)
```

如果挂起也计入 `finished`，看板上"完成率"会被虚高——**审批超时的工作流会被算成成功**。

### 分布统计

`snapshot()["latency_ms"]` 每个键返回 `count` / `p50` / `p95` / `max` / `avg`。
`observe` 的样本上限 2048 条（超出则丢弃最旧的），避免内存无限增长。

---

## 六、BadCase 闭环

[`observability/badcase.py`](../observability/badcase.py) 是闭环的入口。

### 10 类真实生产者

早期实现只有 `Feedback` 这一个数据结构，**没有任何生产者**——`bad_case` 标记永远是 `False`。
现在 10 类来源全部接入：

| 分类 | 生产者 | 代码位置 |
|---|---|---|
| `tool_error` | 工具最终失败 | `ToolRegistry.invoke` 第 10 步 |
| `invalid_parameter` | 参数非法（`PermanentToolError`） | 工具 handler |
| `output_anomaly` | 输出异常 | Agent 输出校验 |
| `policy_denied` | 策略/权限拒绝 | `ToolRegistry.invoke` 第 3/4 步 |
| `ontology_violation` | 本体公理校验失败 | `IncidentWorkflowService._run` |
| `approval_rejected` | 审批被拒 | `approve(approved=False)` |
| `approval_timeout` | 审批超时 | `_approval_timeout` |
| `negative_feedback` | 用户负反馈 | `from_feedback` |
| `llm_fallback` | LLM 降级 | LLM 调用路径 |
| `rate_limited` | 限流 / 资源竞争 | API 依赖、`ToolRegistry`、`_run` |

### 写入接口

```python
def capture(self, category, source, message, *, workflow_id=None,
            trace_id=None, detail=None) -> BadCase:
    """记录一条失败样本。任何层都可以调用，不会向上抛异常。"""
```

**"不会向上抛异常"是刻意的**：BadCase 采集是旁路，**绝不能因为它失败而影响业务**。

### 用户反馈升级

```python
def from_feedback(self, feedback: Feedback) -> BadCase | None:
    """把用户反馈升级为 BadCase（rating <= 2 或显式标记）。"""
    if not feedback.is_negative:
        return None
    return self.capture(BadCaseCategory.NEGATIVE_FEEDBACK, ...)
```

```python
class Feedback(BaseModel):
    trace_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = ""
    bad_case: bool = False
    workflow_id: str | None = None
    submitted_by: str = "anonymous"
    created_at: datetime = ...

    @property
    def is_negative(self) -> bool:
        return self.bad_case or self.rating <= 2
```

`trace_id` 是必填——**没有 Trace 关联的反馈无法用于复盘**。

### 查询与聚合

```python
def list(self, *, limit=None, category=None, unresolved_only=False) -> list[BadCase]
def count(self) -> int
def resolve(self, case_id: str) -> bool
def summary(self) -> dict
```

`summary()` 输出：

```json
{
  "total": 3,
  "unresolved": 3,
  "by_category": {"approval_timeout": 1, "policy_denied": 2},
  "by_source": {"service.restart": 2, "approval": 1}
}
```

`by_source` 按出现次数倒序——**哪个环节最容易出问题一眼可见**。

### 持久化与导出

```python
def _persist(self, case: BadCase) -> None:
    payload = case.model_dump(mode="json")
    if self._store is not None:
        self._store.save(COLLECTION, case.id, payload)
    else:
        with self._lock:
            self._memory.append(case)
    if self._export_path:
        path = Path(self._export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
```

双写：`DocumentStore`（可查询）+ JSONL（离线分析）。`test_badcase_survives_store_roundtrip`
验证新的 `BadCaseCollector` 实例能从同一 store 读回记录。

---

## 七、闭环：BadCase 到下一次迭代

```text
              生产运行
                 │
     ┌───────────┼───────────┬────────────┬──────────────┐
     ▼           ▼           ▼            ▼              ▼
  工具失败   策略拒绝    本体越界    审批拒绝/超时    用户负反馈
     │           │           │            │              │
     └───────────┴───────────┴────────────┴──────────────┘
                             │
                             ▼
                    BadCaseCollector
                  （DocumentStore + JSONL）
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         人工复盘        提示词迭代      词表/规则迭代
      (GET /badcases)  (prompt_version) (ontology 版本)
              │              │              │
              └──────────────┴──────────────┘
                             │
                             ▼
                       下一轮部署
```

**四个具体的迭代动作**：

| 迭代对象 | 输入信号 | 落点 |
|---|---|---|
| 提示词 | `ontology_violation` 的 `raw_root_cause` | 新增提示词版本，`PromptRegistry.activate()` 灰度 |
| 本体词表 | 反复出现的越界取值 | 新增 `ROOT_CAUSES` 取值 + MAJOR/MINOR 版本 |
| 工具重试策略 | 大量 `tool_error` 且都是 `PermanentToolError` | 修正 handler 或参数校验 |
| 权限配置 | 大量 `policy_denied` | 要么是越权尝试（安全事件），要么是权限配置过紧 |

**`policy_denied` 需要特别注意**：它既可能是攻击尝试，也可能是合法的权限配置问题。
两者的处理方式完全相反，**必须人工判断**，不能自动"放宽权限"。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_observability.py`](../tests/test_observability.py) | 12 | Span 树/兄弟/异常落盘/栈复位、指标计数器与百分位、BadCase 全路径、并发安全 |

```bash
pytest tests/test_observability.py -q
```

---

## 未交付

- **OpenTelemetry 导出**：Trace 只存在于进程内 + `DocumentStore` 归档，没有 OTLP exporter。
  接入 OTel 需要一个 `SpanProcessor` 适配层。
- **Prometheus exposition format**：`/metrics` 返回 JSON 快照，需要一个薄适配层才能被直接 scrape。
- **Trace 采样**：全部 Span 都被采集，没有采样策略（高流量下会成为瓶颈）。
- **分布式 Trace 串联**：`trace_id` 不跨服务传播（没有 W3C `traceparent` 解析）。
- **告警规则**：指标是"可读"的，但没有"阈值触发告警"的机制。
- **BadCase 自动标注**：`resolve()` 只标记"已解决"，没有标注"正确根因是什么"。
- **前端看板**：`GET /metrics` 与 `GET /badcases/summary` 提供数据，没有内置 UI。
- **`llm_fallback` 的实际生产者**：分类已定义，但 LLM 降级路径尚未调用 `capture()`
  （降级信息目前通过 Span 属性 `fallback_used` 与 `/metrics` 的 `llm.fallback_rate` 暴露）。
