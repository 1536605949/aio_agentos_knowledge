# 记忆与持久化

> 代码位置：[`memory/`](../memory/)、[`persistence/`](../persistence/)
> 记忆类型：[`memory/types.py`](../memory/types.py) ｜ 存储抽象：[`persistence/base.py`](../persistence/base.py)

> **一句话结论**：Memory 不是工作流状态源。审批、执行、幂等状态**不能**靠 Memory 恢复——
> 这些字段由持久化 `WorkflowState` 拥有。详见 [状态所有权](state-ownership.md)。

---

## 一、四类记忆

```python
class MemoryType(StrEnum):
    SHORT = "short"        # 当前任务临时上下文
    LONG = "long"          # 长期业务经验
    VECTOR = "vector"      # 可检索历史知识的向量引用
    EPISODIC = "episodic"  # 一次故障的完整处置记录
```

```python
class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    memory_type: MemoryType
    content: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list)
```

四个子类只是把 `memory_type` 固定下来并加上类型特有字段：

| 类型 | 类 | 特有字段 | 生命周期 |
|---|---|---|---|
| `short` | `ShortMemory` | — | 单次会话 / 单次故障处置 |
| `long` | `LongMemory` | — | 长期，跨故障累积 |
| `vector` | `VectorMemory` | `embedding_ref: str \| None` | 长期，可检索 |
| `episodic` | `EpisodicMemory` | `outcome: str \| None` | 一次故障一条，不可变 |

**注意 `VectorMemory` 只存 `embedding_ref`，不存向量本身**——
向量由外部向量库管理，这里只保留引用。这是刻意的边界：
本项目不内置向量数据库。

---

## 二、当前真实写入的记忆

**只有 `EpisodicMemory` 有生产者**。这是刻意的最小实现。

`temporal/workflow.py::_write_episode()` 在四个终态各写一条：

| 触发点 | `outcome` | 何时 |
|---|---|---|
| `_execute_remediation` 完成 | `"completed"` | 修复执行成功，或无副作用动作 |
| `approve(approved=False)` | `"rejected"` | 审批被拒 |
| `_approval_timeout` | `"approval_timeout"` | 审批超时（默认拒绝） |
| `_fail` | `"failed"` | 任意异常导致工作流失败 |

```python
def _write_episode(self, state: WorkflowState, outcome: str) -> None:
    self.memory.put(
        EpisodicMemory(
            workflow_id=state.workflow_id,
            content={
                "intent": state.intent,
                "alarm": state.alarm,
                "severity": state.severity,
                "root_cause": state.root_cause,
                "confidence": state.confidence,
                "proposed_action": state.proposed_action,
                "remediation_result": state.remediation_result,
                "ontology_errors": state.ontology_errors,
            },
            outcome=outcome,
            tags=[outcome, state.intent],          # ← 可按 outcome / intent 检索
        )
    )
```

`tags=[outcome, intent]` 让"所有失败的 diagnose 类处置"这类查询变得直接。

`aio-agentos demo` 会把这个打印出来：

```json
[
  {
    "id": "...",
    "workflow_id": "...",
    "memory_type": "episodic",
    "content": {
      "intent": "diagnose",
      "root_cause": "upstream_timeout",
      "confidence": 0.7,
      "proposed_action": "restart_service",
      "remediation_result": {"executed": true, "mode": "reference"}
    },
    "outcome": "completed",
    "tags": ["completed", "diagnose"]
  }
]
```

### 为什么 Short / Long / Vector 没有生产者

| 类型 | 为什么现在没有 |
|---|---|
| `short` | 单次 Agent 调用的临时上下文已经由 `AgentContext` 承载（内存、不持久化），再写一份 Memory 是重复 |
| `long` | "从历史故障中提炼长期经验"需要一个提炼过程（聚合、摘要、去重），不是简单的 `put()` |
| `vector` | 需要向量化模型与向量库，属于外部依赖 |

**这三类是显式声明的接口，不是"已实现的功能"。** 见文末"未交付"。

`AgentSpec.memory_policy` 已经声明了每个 Agent 的读写意图：

```python
memory_policy=MemoryPolicy(read_types=["short", "episodic"], write_types=["short"])
```

但**当前 `BaseAgent` 不执行这个策略**——它是一个声明，等待实现。

---

## 三、存储实现

```python
class InMemoryMemoryStore:
    """Thread-safe reference store. Replace behind this interface in production."""

    def __init__(self) -> None:
        self._records: dict[str, list[MemoryRecord]] = defaultdict(list)
        self._lock = RLock()

    def put(self, record: MemoryRecord) -> MemoryRecord: ...

    def list(self, workflow_id: str, memory_type: MemoryType | None = None) -> list[MemoryRecord]: ...
```

按 `workflow_id` 分桶，可按 `memory_type` 过滤。用 `threading.RLock`（不是 `asyncio.Lock`），
因为写入发生在 `_write_episode` 这类同步方法里。

`Runtime.memory` 是一个 `InMemoryMemoryStore` 实例，由 [`bootstrap.py`](../bootstrap.py) 创建并注入
`IncidentWorkflowService`。

**注意**：`InMemoryMemoryStore` **不落 `DocumentStore`**——重启即丢。
这与 `WorkflowState` 形成对比，也正是为什么"审批状态不能靠 Memory 恢复"。

---

## 四、`DocumentStore`：持久化的真正载体

Memory 是"记录发生了什么"，`DocumentStore` 是"保证状态不丢"。两者职责完全不同。

[`persistence/base.py`](../persistence/base.py) 用一个「JSON 文档集合」模型承载全部需要持久化的数据：

| collection | 承载内容 | 写入方 |
|---|---|---|
| `workflows` | `WorkflowState` 快照 | `temporal/workflow.py` |
| `traces` | Span 归档（跨重启回读调用树） | `temporal/workflow.py` |
| `workflow_idempotency` | 幂等键 → `workflow_id` | `temporal/workflow.py` |
| `tool_idempotency` | 工具幂等键 → 执行结果 | `tools/registry.py` |
| `badcases` | BadCase 记录 | `observability/badcase.py` |

### 协议

```python
@runtime_checkable
class DocumentStore(Protocol):
    def save(self, collection: str, key: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def load(self, collection: str, key: str) -> dict[str, Any] | None: ...
    def query(self, collection: str, *, limit: int | None = None,
              newest_first: bool = True) -> list[dict[str, Any]]: ...
    def delete(self, collection: str, key: str) -> bool: ...
    def count(self, collection: str) -> int: ...
    def collections(self) -> list[str]: ...
    def close(self) -> None: ...
```

7 个方法——刻意保持最小。没有事务、没有 join、没有二级索引查询，
因为**这些数据都是"按 key 读、按时间倒序列"的访问模式**。

### 两个实现

| 后端 | 类 | 特性 |
|---|---|---|
| `memory`（默认） | `InMemoryDocumentStore` | 重启即丢；开发与测试 |
| `sqlite` | `SqliteDocumentStore` | WAL 模式、UPSERT **保留 `created_at`**、`documents` 表 + 索引 |

元数据三件套：

```python
META_KEY = "_key"
META_CREATED = "_created_at"
META_UPDATED = "_updated_at"
```

SQLite 实现里有一个容易忽略的细节——**更新不覆盖创建时间**：

```python
def save(self, collection, key, payload):
    existing = bucket.get(key)
    record = stamp_new(payload, key)
    if existing and META_CREATED in existing:
        record[META_CREATED] = existing[META_CREATED]      # ← 保留原创建时间
    bucket[key] = record
```

`tests/test_persistence.py` 有一个**重开文件验证跨重启**的用例，
确认关闭连接再打开后数据仍在——这是"持久化"和"进程内字典"的分界线。

切换后端：

```bash
AIO_AGENTOS_STORE_BACKEND=sqlite
AIO_AGENTOS_SQLITE_PATH=./var/aio.db
```

---

## 五、持久化边界图

```text
┌──────────────────────── 进程内（重启即丢） ─────────────────────────┐
│                                                                   │
│  AgentContext          单次 Agent 调用，用完即弃                     │
│  IncidentGraphState    一次推理 Activity 内                         │
│  AgentTrace            Span 采集（但会归档到 traces collection）      │
│  InMemoryMemoryStore   Episodic / Short / Long / Vector 记录        │
│  TTLCache              工具结果缓存                                 │
│  TokenBucketLimiter    令牌桶状态                                   │
│  ResourceLockManager   锁与等待队列                                 │
│  MetricsCollector      指标累加                                     │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘

┌──────────────── DocumentStore（可跨重启） ────────────────────────┐
│                                                                   │
│  workflows             WorkflowState  ← 编排状态唯一事实源           │
│  traces                Span 归档      ← 跨重启回读调用树              │
│  workflow_idempotency  幂等键映射     ← 跨重启不重复建工作流           │
│  tool_idempotency      工具幂等结果   ← 跨重启不重复执行副作用         │
│  badcases              BadCase 记录   ← 闭环输入跨重启保留            │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

**判断某份数据该放哪边的标准**：

| 问题 | 放 `DocumentStore` | 留进程内 |
|---|---|---|
| 丢失会导致业务错误吗？ | ✅ | |
| 需要跨重启恢复吗？ | ✅ | |
| 只是性能优化（缓存）？ | | ✅ |
| 只是当前调用的临时上下文？ | | ✅ |
| 只是运行时计数？ | | ✅ |

按这个标准：**审批状态必须落库**（丢了审批人会看到"工作流不存在"），
**令牌桶状态可以不落库**（丢了只是重新获得满配额）。

---

## 六、为什么审批状态必须持久化

这是本设计最重要的一条推演。假设 `WAITING_APPROVAL` 只在内存里：

```text
t0   告警 A-1001 推理完成
     根因 upstream_timeout → 动作 restart_service → 高风险 → 需要审批
     status = WAITING_APPROVAL（仅内存）

t1   服务因部署 / OOM / 节点迁移而重启
     内存中的 states / traces / 超时任务全部丢失

t2   审批人收到通知，点击批准
     → 系统返回 404 "workflow not found"
     → 审批人不知道发生了什么
     → 故障无人处理

t3   更糟的情况：审批人重新提交一次告警
     → 推理重跑 → 又生成一个需要审批的工作流
     → 审批人再批一次
     → 服务被重启两次（幂等键不同）
```

持久化后：

```text
t0   status = WAITING_APPROVAL，落库
     approval_deadline 落库
     trace_id 落库
     Trace 归档到 traces collection

t1   重启

t2   审批人批准
     → GET /workflow/{id} 从 store 回读状态 ✓
     → approve() 读到 WAITING_APPROVAL ✓
     → 执行时用 idempotency_key = f"{workflow_id}:{tool}" 保证只执行一次 ✓
     → GET /workflow/{id}/trace 从 traces collection 回读完整调用树 ✓
```

**`trace_id` 也必须持久化**，否则重启后无法把新产生的 Span 关联到原 Trace。

`IncidentWorkflowService.trace()` 的回读逻辑：

```python
def trace(self, workflow_id: str) -> AgentTrace:
    """返回该工作流的 Trace。进程内没有时从持久化归档回读。"""
    state = self.get(workflow_id)
    trace = self.traces.get(workflow_id)
    if trace is not None:
        return trace
    record = self.store.load(TRACE_COLLECTION, workflow_id)
    if record is None:
        trace = AgentTrace(trace_id=state.trace_id)
        self.traces[workflow_id] = trace
        return trace
    trace = AgentTrace.from_dict(record)      # ← 反序列化重建
    self.traces[workflow_id] = trace
    return trace
```

`AgentTrace.from_dict()` 用 `Span.model_validate` 重建每个 Span，因此 `parent_span_id`
关系被完整保留——**重启后调用树结构不变**。

---

## 七、四类记忆的预期用法（设计意图）

虽然当前只有 Episodic 落地，其余三类的设计意图是明确的：

| 类型 | 预期用法 | 需要补什么 |
|---|---|---|
| `Short` | 单次处置内的跨 Agent 上下文（例如"已确认服务在 k8s 集群 A"） | 需要在 `BaseAgent` 里实现 `memory_policy` 的读写 |
| `Long` | 从历史 Episodic 中提炼的经验（例如"checkout 的 timeout 90% 是 payment 侧"） | 需要聚合/摘要作业 |
| `Vector` | 相似历史故障检索，为推理提供 few-shot 参考 | 需要 embedding 模型 + 向量库 |
| `Episodic` | ✅ **已实现**——一次故障的完整处置记录 | — |

**Episodic 优先实现是刻意的**：它是最基础的一类（一次故障一条，结构固定，不需要检索），
而且它已经能支撑两个真实用途——人工复盘、以及作为 Long/Vector 的原始素材。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_persistence.py`](../tests/test_persistence.py) | 6 | 内存/SQLite 往返、**重开文件验证跨重启**、`created_at` 保留、后端选择 |
| [`tests/test_workflow_lifecycle.py`](../tests/test_workflow_lifecycle.py) | 16 | 状态机、**持久化恢复**、Episodic 写入 |

```bash
pytest tests/test_persistence.py tests/test_workflow_lifecycle.py -q
```

---

## 未交付

- **`Short` / `Long` / `Vector` 记忆的生产者**：三类只定义了数据结构，没有写入路径。
- **`AgentSpec.memory_policy` 的执行**：策略是声明，`BaseAgent` 不读写 Memory。
- **记忆的持久化**：`InMemoryMemoryStore` 不落 `DocumentStore`，Episodic 记录重启即丢。
- **记忆检索**：只有 `list(workflow_id, memory_type)`，没有按 tag / 内容 / 时间范围检索。
- **向量检索**：`VectorMemory.embedding_ref` 只是字段，没有 embedding 与近邻搜索。
- **记忆衰减与压缩**：没有 TTL、没有摘要、没有容量上限。
- **PostgreSQL / 向量库实现**：`DocumentStore` 协议已就绪，但没有非 SQLite 的实现。
- **数据库迁移**：SQLite 表在首次使用时创建，没有 schema 版本管理与迁移脚本。
