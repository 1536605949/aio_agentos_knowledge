# 弹性与横切能力

> 代码位置：[`resilience/`](../resilience/)、[`concurrency/`](../concurrency/)、[`persistence/`](../persistence/)
> 装配位置：[`bootstrap.py`](../bootstrap.py) ｜ 调用链：[`tools/registry.py`](../tools/registry.py)

「横切能力」这个词容易被误解成"几个可选的中间件"。在本项目里它们是**必须回答的具体问题**：

| 问题 | 能力 | 落点 |
|---|---|---|
| 上游模型网关被打爆怎么办？ | 令牌桶限流 | `resilience/ratelimit.py` |
| 同一服务被反复查询依赖关系，每次都要打下游？ | TTL + LRU 缓存 | `resilience/cache.py` |
| 两个故障同时重启同一个服务，互相干扰？ | 资源锁（FIFO + 超时） | `concurrency/locks.py` |
| 某个工具持续失败，每次都要等超时？ | 熔断器 | `tools/registry.py` |
| 重试把「重启服务」执行了三次？ | 幂等键 + 错误分级 | `tools/registry.py` |
| 进程重启后工作流状态全丢？ | `DocumentStore` 抽象 | `persistence/` |

---

## 一、它们在调用链中的位置

这是本项目最值得看的一张图。`ToolRegistry.invoke()` 的 10 个步骤**顺序即设计**：

```text
ToolRegistry.invoke(name, payload, principal, trace, approved, idempotency_key)
  │
  ├─ 1. 存在性检查        未注册 → ToolNotFound（不做隐式放行）
  ├─ 2. 熔断检查          连续失败达阈值 → CircuitOpen
  │
  ├─ 3. 治理检查  ◀────── 在重试循环之外！
  ├─ 4. 权限检查           required_permission ∈ principal 权限集合
  │
  ├─ 5. 出站限流  ◀────── 令牌桶，按 tool:{name} 隔离
  ├─ 6. 幂等查询  ◀────── idempotency_key 命中即返回
  ├─ 7. 结果缓存  ◀────── cacheable 工具按 payload 哈希
  │
  ├─ 8. 资源锁    ◀────── resource_field 指定的资源互斥
  │     │
  │     └─ 9. 重试循环 ── 仅对瞬时错误重试，PermanentToolError 立即失败
  │           └─ trace.span("tool.invoke", kind=TOOL, attempt=N)
  │
  └─ 10. 失败归档 ─────── 最终失败写 BadCase，构成闭环输入
```

**第 3 步的位置是最关键的**。早期实现把治理检查放在重试循环内部，后果是：

```text
策略拒绝「未经审批的高风险动作」
  → 被当成瞬时故障
  → 重试 3 次
  → 每次重试都重新走一遍策略判定（都拒绝）
  → 最终报 ToolError 而不是 ToolPolicyDenied
  → 运维看到的是"工具不稳定"，而不是"有人试图越权"
```

现在策略拒绝**在循环外**，`ToolPolicyDenied` 一次抛出，且计数进 `policy_denials` 并写 BadCase。

---

## 二、令牌桶限流

[`resilience/ratelimit.py`](../resilience/ratelimit.py) 用**惰性补充（lazy refill）**实现，不需要后台任务：

```python
def _refill(self, bucket: _Bucket, now: float) -> None:
    elapsed = max(0.0, now - bucket.updated_at)
    bucket.tokens = min(float(self.capacity), bucket.tokens + elapsed * self.rate_per_second)
    bucket.updated_at = now
```

每次 `acquire()` 时按"距上次访问过了多久"补令牌。好处是**无状态多副本部署友好**——
没有定时器，没有跨进程同步。

### 两类限流对象

| 方向 | key | 位置 | 保护对象 |
|---|---|---|---|
| 入站 | `api:{caller}` | [`api/app.py`](../api/app.py) 的 `rate_limit` 依赖 | 仅作用于**写端点**，避免读端点被轮询打爆 |
| 出站 | `tool:{name}` | `ToolRegistry.invoke` 第 5 步 | 上游工具配额 |
| 出站 | `workflow:reasoning` | [`temporal/workflow.py`](../temporal/workflow.py) 的 `_run` | LLM 配额 |

调用方身份取自 `X-API-Key`，缺省回落到客户端 IP：

```python
caller = x_api_key or (request.client.host if request.client else "anonymous")
```

### 非阻塞 vs 阻塞

| 方法 | 语义 | 使用者 |
|---|---|---|
| `acquire(key, tokens)` | 立即返回 `RateLimitDecision`，不等待 | API 依赖、工具调用（拒绝即抛错） |
| `wait_for(key, tokens, timeout)` | 阻塞至取到令牌或超时 | 适合可容忍等待的场景 |

`RateLimitDecision` 带 `retry_after_seconds`，API 层据此设置 `Retry-After` 响应头：

```python
raise HTTPException(429, detail="rate limit exceeded",
                    headers={"Retry-After": str(max(1, ceil(verdict.retry_after_seconds)))})
```

被限流的请求也会写 BadCase（`BadCaseCategory.RATE_LIMITED`）——**限流事件是需要被复盘的**，
它说明有人在异常调用，或者配额设置不合理。

---

## 三、TTL + LRU 缓存

[`resilience/cache.py`](../resilience/cache.py) 用 `OrderedDict` 实现 LRU：

```python
async def get(self, key: str) -> T | None:
    entry = self._entries.get(key)
    if entry is None:
        self._stats.misses += 1
        return None
    expires_at, value = entry
    if expires_at <= time.monotonic():
        del self._entries[key]                      # 过期即删
        self._stats.expirations += 1
        return None
    self._entries.move_to_end(key)                  # LRU：命中即提到队尾
    self._stats.hits += 1
    return value
```

容量上限通过 `popitem(last=False)` 淘汰队首（最久未使用）。

### 分层策略

| 层 | 内容 | TTL | 位置 |
|---|---|---|---|
| L1 工具结果 | 只读工具输出 | 默认 30s | `TTLCache` |
| L2 注册表 | 本体 / 技能 / 提示词 | 无 TTL（启动加载一次） | 进程内常驻 |
| L3 外部缓存 | 跨副本共享 | — | 生产替换为 Redis（实现同一接口） |

### 工具结果缓存的开启条件

只有 `ToolSpec.cacheable=True` 的工具才走缓存，且 key 是 payload 的**稳定哈希**：

```python
def _stable_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
```

`sort_keys=True` 保证 `{"a":1,"b":2}` 与 `{"b":2,"a":1}` 命中同一缓存条目。

当前开启缓存的工具：`topology.lookup`（TTL 60s）、`logs.search`（用全局 TTL）。
**所有带副作用的工具都显式不开缓存**——缓存一个"重启服务"的返回值毫无意义且危险。

### 命中率是可观测的

```json
"cache": {"hits": 12, "misses": 5, "evictions": 0, "expirations": 3, "size": 9, "hit_rate": 0.7059}
```

`hit_rate` 直接暴露在 `GET /metrics`。`invalidate_prefix()` 支持按前缀批量失效
（例如服务拓扑变更后 `invalidate_prefix("tool:topology.lookup:")`）。

---

## 四、资源锁

[`concurrency/locks.py`](../concurrency/locks.py) 解决的问题是：**两个故障同时处置同一个服务**。

```python
async with locks.acquire("service:checkout", holder="remediation"):
    await restart()      # 这段时间内其他持有者只能排队
```

### 三个保证

| 保证 | 实现 |
|---|---|
| 互斥 | 每个 resource 一个 `asyncio.Lock` |
| FIFO 公平 | `waiters: deque[str]` 记录排队顺序，超时错误信息里带 `queued=N` |
| 超时可控 | `asyncio.wait_for(lock.acquire(), timeout=...)` → `ResourceLockTimeout` |

用法是 `@asynccontextmanager`，因此**异常路径也会释放锁**（`finally: state.lock.release()`）。

### 两处使用

| 位置 | resource key | 目的 |
|---|---|---|
| `ToolRegistry._maybe_lock` | `{tool}:{payload[resource_field]}` | 工具级：`service.restart` 的 `resource_field="service"` |
| `temporal/workflow.py::_execute_remediation` | `service:{service}` | 工作流级：同一服务的修复动作串行 |

超时后抛 `ToolError("service 'checkout' is busy: ...")`，并写 BadCase（带 `hint: resource_contention`）——
**资源竞争是需要人工介入的信号**，不能静默重试掉。

---

## 五、熔断器

熔断器内联在 `ToolRegistry` 中，不单独成模块——因为它与重试逻辑共享同一份失败计数。

```python
except Exception as exc:
    entry.failures += 1
    entry.consecutive_failures += 1
    if entry.consecutive_failures >= self.circuit_threshold:   # 默认 3
        entry.circuit_open_until = monotonic() + self.cooldown_seconds   # 默认 30s
        break
```

打开后，**下一次调用在进入任何逻辑前就短路**：

```python
if entry.circuit_open_until > monotonic():
    raise CircuitOpen(name)
```

成功后 `consecutive_failures = 0`（半开状态自动恢复）。也可通过 `reset_circuit(name)` 手工重置。

`GET /tools` 里每个工具都带 `circuit_open` 布尔值，`GET /metrics` 的 `tools` 字段带完整统计。

---

## 六、幂等

幂等分两层，缺一不可：

### 第一层：错误分级

```python
class TransientToolError(ToolError):   # 网络抖动、下游 5xx、超时 → 会重试
class PermanentToolError(ToolError):   # 参数非法、资源不存在 → 立即失败
```

`PermanentToolError` 直接 `break` 出重试循环。看 [`tools/catalog.py`](../tools/catalog.py) 的例子：

```python
async def _restart_service(payload):
    service = payload.get("service")
    if not service:
        raise PermanentToolError("service.restart requires a 'service' field")
```

参数缺失重试 3 次仍然是缺参数——重试只是浪费时间并放大副作用风险。

### 第二层：幂等键落库

```python
key = idempotency_key or (payload.get("idempotency_key") if entry.spec.idempotent else None)
if entry.spec.idempotent and key:
    cached = self._idempotent_lookup(name, str(key))
    if cached is not None:
        entry.idempotent_hits += 1
        return cached      # 直接返回上次结果，handler 不再执行
```

关键点：**幂等记录存进 `DocumentStore`（collection `tool_idempotency`），不是进程内 dict**。
因此进程重启后重复提交仍然返回同一结果。

工作流层的幂等键是 `f"{workflow_id}:{tool}"`——同一个工作流对同一个工具只会真正执行一次。

### 端到端幂等

`POST /alarm` 的 `idempotency_key` 走的是另一套（collection `workflow_idempotency`），
把 key 映射到 `workflow_id`：

```python
existing = service.resolve_idempotency(key)
if existing is not None:
    return _response(existing)      # 不创建新工作流
```

`aio-agentos check` 会实测这一条：同一 key 调用两次，断言 `first == second`。

---

## 七、持久化抽象

[`persistence/base.py`](../persistence/base.py) 用一个「JSON 文档集合」模型承载全部数据，
避免为每类数据各写一套仓储：

| collection | 承载内容 | 写入方 |
|---|---|---|
| `workflows` | `WorkflowState` 快照 | `temporal/workflow.py` |
| `traces` | Span 归档（跨重启回读调用树） | `temporal/workflow.py` |
| `workflow_idempotency` | 幂等键 → workflow_id | `temporal/workflow.py` |
| `tool_idempotency` | 工具幂等键 → 执行结果 | `tools/registry.py` |
| `badcases` | BadCase 记录 | `observability/badcase.py` |

`DocumentStore` 是 `@runtime_checkable` 的 `Protocol`，7 个方法：`save` / `load` / `query` / `delete` / `count` / `collections` / `close`。

### 两个实现

| 后端 | 类 | 特性 |
|---|---|---|
| `memory`（默认） | `InMemoryDocumentStore` | 重启即丢；开发与测试 |
| `sqlite` | `SqliteDocumentStore` | WAL 模式、UPSERT **保留 `created_at`**、`documents` 表 + 索引 |

SQLite 实现里有一个容易忽略的细节：更新已有记录时不覆盖 `_created_at`：

```python
record = stamp_new(payload, key)
if existing and META_CREATED in existing:
    record[META_CREATED] = existing[META_CREATED]
```

`tests/test_persistence.py` 有一个**重开文件验证跨重启**的用例，确认关闭再打开后数据仍在。

切换只需一个环境变量：

```bash
AIO_AGENTOS_STORE_BACKEND=sqlite
AIO_AGENTOS_SQLITE_PATH=./var/aio.db
```

生产换 PostgreSQL 只需实现同一协议，`bootstrap.build_runtime()` 改一行。

---

## 八、为什么集中在组装根

[`bootstrap.py`](../bootstrap.py) 的 `build_runtime()` 把限流器、缓存、锁、存储、指标、BadCase 收集器
**一次性创建并注入**到 `ToolRegistry` 与 `IncidentWorkflowService`：

```python
tools = build_default_tool_registry(
    circuit_threshold=settings.tool_circuit_threshold,
    cooldown_seconds=settings.tool_circuit_cooldown_seconds,
    cache=cache, limiter=limiter, locks=locks,
    badcases=badcases, metrics=metrics, store=store,
)
```

早期实现里这些对象是"到处 new"的，导致：同一份配置被读多次、缓存实例不共享（命中率永远为 0）、
持久化根本没有注入点。集中装配后，**替换任意一层只需改 `bootstrap.py` 一处**。

---

## 相关测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [`tests/test_resilience.py`](../tests/test_resilience.py) | 10 | 令牌桶 burst / key 隔离 / refill、TTL 过期、LRU 淘汰、前缀失效 |
| [`tests/test_concurrency.py`](../tests/test_concurrency.py) | 5 | 互斥、FIFO 公平、超时、统计、不同资源不阻塞 |
| [`tests/test_persistence.py`](../tests/test_persistence.py) | 6 | 内存/SQLite 往返、**重开文件验证跨重启**、后端选择 |
| [`tests/test_tools_resilience.py`](../tests/test_tools_resilience.py) | 17 | 治理位置、重试分级、熔断、幂等 + store、缓存、限流、资源锁、BadCase |

```bash
pytest tests/test_resilience.py tests/test_concurrency.py tests/test_persistence.py tests/test_tools_resilience.py -q
```

---

## 未交付

- **分布式限流 / 分布式锁**：当前实现的状态在进程内，多副本部署时每副本独立配额。
  生产需要 Redis 实现（`TokenBucketLimiter` / `ResourceLockManager` 的接口已按可替换设计）。
- **熔断的半开探测**：当前是"冷却期结束 + 下一次调用成功"即恢复，没有主动探测流量。
- **重试预算（retry budget）**：没有"整体重试率超过阈值就停止重试"的机制。
- **缓存预热与多级回写**：`TTLCache` 是纯本地 L1，没有 L2 回写逻辑。
