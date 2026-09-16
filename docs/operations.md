# 运行与配置

> 代码位置：[`config.py`](../config.py)、[`cli.py`](../cli.py)、[`bootstrap.py`](../bootstrap.py)
> 样例文件：[`.env.example`](../.env.example)

---

## 一、安装

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"            # 开发：含 pytest / ruff / import-linter
```

按需追加可选依赖：

| extra | 内容 | 何时需要 |
|---|---|---|
| `openai` | `httpx` | 用真实 LLM provider（OpenAI / DeepSeek / 自建网关） |
| `langchain` | `langchain-core` + `langchain-openai` | 用 LangChain 作 ChatModel 适配层 |
| `orchestration` | `langgraph` + `temporalio` + `mcp` | 生产级持久化编排与 MCP 接入 |
| `dev` | `pytest` + `pytest-asyncio` + `httpx` + `ruff` + `import-linter` | 本地开发与 CI |

```bash
pip install -e ".[dev,openai]"            # 常用组合
pip install -e ".[dev,orchestration]"     # 接 Temporal Server
```

**默认安装（不带任何 extra）即可完整运行**——确定性 LLM + 内存存储，零外部依赖。

---

## 二、CLI

安装后提供 `aio-agentos` 命令（也可 `python -m cli`）：

```bash
aio-agentos check     # 自检：把所有主要模块真的跑一遍
aio-agentos demo      # 端到端纵向切片演示
aio-agentos serve     # 启动 FastAPI 服务
```

### `check` —— 自检

不是"import 一下看有没有报错"，而是**真的调用**。它会依次执行：

| 步骤 | 内容 | 失败判定 |
|---|---|---|
| 1 | 打印配置、运行时健康、本体摘要、路由计划 | — |
| 2 | 工具四条探针：低风险放行 / 高风险未审批被拒 / 重试成功 / 永久错误不重试 | 出现 `BUG` 前缀或高风险未被拒 |
| 3 | 幂等重放：同 key 两次调用结果必须相同 | 结果不一致 |
| 4 | 端到端：`告警 → 推理 → 审批 → 受控执行` | 状态非 `completed` |
| 5 | 本体校验错误必须为空 | `ontology_errors` 非空 |
| 6 | Trace 必须是父子树（`max_depth >= 2`） | Trace 扁平 |
| 7 | 打印 BadCase 汇总、指标计数器、LLM 用量 | — |

退出码 `0` 表示通过，`1` 表示失败——可直接作为部署前置门禁：

```bash
aio-agentos check || exit 1
```

### `demo` —— 端到端演示

```bash
aio-agentos demo
aio-agentos demo --service payment      # 指定受影响服务
```

打印内容：运行时健康 → 推理结果（含 `steps`、`ontology_errors`）→ 审批 → 受治理执行结果
→ **Trace 调用树（缩进渲染）** → Episodic Memory → BadCase 汇总 → LLM 用量。

### `serve` —— 启动 API

```bash
aio-agentos serve                                   # 127.0.0.1:8000
aio-agentos serve --host 0.0.0.0 --port 8080
aio-agentos serve --reload                          # 开发热重载
```

等价于 `uvicorn api.app:app`。交互式文档在 `/docs`，OpenAPI JSON 在 `/openapi.json`。

---

## 三、环境变量全表

所有环境变量读取**集中在** [`config.py`](../config.py) 的 `Settings.from_env()`——
项目里没有散落的 `os.getenv`。完整样例见 [`.env.example`](../.env.example)。

### 运行环境与鉴权

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_ENV` | `development` | `development` / `staging` / `production` |
| `AIO_AGENTOS_ALLOW_INSECURE` | `false` | 为 `true` 时允许无密钥启动（仅本地） |
| `AIO_AGENTOS_API_KEY` | 未设置 | 设置后所有端点要求 `X-API-Key` |
| `AIO_AGENTOS_APPROVER_KEY` | 未设置 | 设置后审批端点额外要求 `X-Approver-Key` |

### LLM

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_LLM_PROVIDER` | `deterministic` | `deterministic` / `openai` / `deepseek` / `langchain` |
| `AIO_AGENTOS_LLM_MODEL` | `deterministic-rule-v1` | 模型名 |
| `AIO_AGENTOS_LLM_API_KEY` | 未设置 | 真实 provider 必填 |
| `AIO_AGENTOS_LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点 |
| `AIO_AGENTOS_LLM_TIMEOUT_SECONDS` | `30` | 单次调用超时 |
| `AIO_AGENTOS_LLM_MAX_RETRIES` | `2` | 5xx / 429 退避重试次数 |

### 编排

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_APPROVAL_TIMEOUT_SECONDS` | `86400`（24h） | 审批超时后**默认拒绝** |
| `AIO_AGENTOS_REASONING_TIMEOUT_SECONDS` | `120` | 内层推理图整体超时 |
| `AIO_AGENTOS_MAX_GRAPH_STEPS` | `24` | 图节点上限，超出即判失败 |

### 横切能力

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_RATE_LIMIT_PER_MINUTE` | `120` | 令牌桶补充速率 |
| `AIO_AGENTOS_RATE_LIMIT_BURST` | `30` | 桶容量（突发上限） |
| `AIO_AGENTOS_CACHE_TTL_SECONDS` | `30` | 工具结果缓存 TTL |
| `AIO_AGENTOS_CACHE_MAX_ENTRIES` | `512` | 缓存条目上限（LRU 淘汰） |
| `AIO_AGENTOS_RESOURCE_LOCK_TIMEOUT_SECONDS` | `10` | 资源锁等待超时 |
| `AIO_AGENTOS_TOOL_CIRCUIT_THRESHOLD` | `3` | 连续失败多少次后熔断 |
| `AIO_AGENTOS_TOOL_CIRCUIT_COOLDOWN_SECONDS` | `30` | 熔断冷却时长 |

### 持久化

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_STORE_BACKEND` | `memory` | `memory` / `sqlite` |
| `AIO_AGENTOS_SQLITE_PATH` | `aio_agentos.db` | SQLite 文件路径 |

### 可观测与协议

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIO_AGENTOS_TRACE_EXPORT_PATH` | 未设置 | 设置后 Trace 额外导出 JSONL |
| `AIO_AGENTOS_BADCASE_EXPORT_PATH` | 未设置 | 设置后 BadCase 额外导出 JSONL |
| `AIO_AGENTOS_A2A_ENDPOINT` | `http://localhost:8000` | AgentCard 里对外声明的端点 |

### Temporal（仅生产编排）

| 变量 | 默认 |
|---|---|
| `TEMPORAL_ADDRESS` | `localhost:7233` |
| `TEMPORAL_NAMESPACE` | `default` |
| `TEMPORAL_TASK_QUEUE` | `aio-agentos` |

---

## 四、三种部署形态

### 形态 A：本地参考实现（默认）

```bash
pip install -e ".[dev]"
aio-agentos serve
```

| 维度 | 状态 |
|---|---|
| 编排 | `IncidentWorkflowService`（Temporal 语义的进程内实现） |
| 存储 | 内存（重启即丢）或 SQLite |
| LLM | 确定性规则实现 |
| 外部依赖 | 无 |

**适合**：本地开发、演示、CI、教学。

### 形态 B：单机可持久化

```bash
export AIO_AGENTOS_STORE_BACKEND=sqlite
export AIO_AGENTOS_SQLITE_PATH=./var/aio.db
export AIO_AGENTOS_API_KEY=$(openssl rand -hex 16)
export AIO_AGENTOS_APPROVER_KEY=$(openssl rand -hex 16)
export AIO_AGENTOS_LLM_PROVIDER=openai
export AIO_AGENTOS_LLM_API_KEY=sk-xxx
export AIO_AGENTOS_LLM_MODEL=deepseek-chat

aio-agentos check && aio-agentos serve
```

关键变化：

- 状态、Trace、幂等键**跨重启保留**（SQLite WAL）
- 鉴权开启，审批端点需要独立的 `X-Approver-Key`
- LLM 换成真实模型（`ResilientLLM` 仍保留确定性降级）

**适合**：内网试用、单机 PoC。

### 形态 C：Temporal 生产编排

```bash
pip install -e ".[orchestration]"

export TEMPORAL_ADDRESS=your-temporal:7233
export TEMPORAL_NAMESPACE=default
export TEMPORAL_TASK_QUEUE=aio-agentos

python -m examples.temporal_worker     # 启动 Worker
```

对应实现是 [`temporal/production.py`](../temporal/production.py)，与本地参考实现**共享同一套领域组件**
（本体、工具注册表、推理图），只替换持久化机制：

| 关注点 | 形态 A/B（本地参考） | 形态 C（Temporal SDK） |
|---|---|---|
| 持久化 | `DocumentStore`（SQLite / 内存） | Temporal 事件历史 |
| 审批信号 | `asyncio.Task` + `approve()` | `@workflow.signal approval(...)` |
| 超时 | `asyncio.sleep` | `workflow.wait_condition(timeout=...)` |
| 重试 | `ToolRegistry` 的错误分级 | Temporal `RetryPolicy` + 工具重试 |

**关键约束**：Temporal Workflow 代码必须确定性，因此所有副作用（LLM 调用、工具执行、IO）
都放在 Activity 里——`reasoning_activity` 与 `remediation_activity`。Workflow 本体只做编排与状态迁移。

治理路径完全一致：`remediation_activity` 只是把参数转发给 `runtime.tools.invoke`，
**策略检查、幂等键、资源锁仍在 `ToolRegistry` 内部完成**。

---

## 五、安全配置自检

`Settings.validate_security_posture()` 返回风险项清单，挂到 `GET /healthz` 的 `security_warnings`：

| 触发条件 | 提示 |
|---|---|
| 未配置 `AIO_AGENTOS_API_KEY` | `所有 API 端点将拒绝请求` |
| 未配置 `AIO_AGENTOS_APPROVER_KEY` | `审批端点将拒绝请求` |
| `ENV=production` 且 `LLM_PROVIDER=deterministic` | `生产环境仍在使用 deterministic LLM provider` |
| `ENV=production` 且 `STORE_BACKEND=memory` | `生产环境仍在使用 memory store backend（重启即丢失状态）` |

```bash
curl localhost:8000/healthz
```

```json
{
  "status": "ok",
  "environment": "development",
  "llm_provider": "deterministic",
  "store_backend": "memory",
  "ontology_version": "3.5.0",
  "skills": 6, "tools": 9, "agents": 6,
  "security_warnings": [
    "AIO_AGENTOS_API_KEY 未配置：所有 API 端点将拒绝请求",
    "AIO_AGENTOS_APPROVER_KEY 未配置：审批端点将拒绝请求"
  ]
}
```

**未配置密钥时系统仍可启动，但风险是显式列出的**——这是刻意的设计选择：
静默放行比拒绝服务更危险。

---

## 六、质量门禁

CI 三道关卡，本地一键复现：

```bash
ruff check .        # 静态检查
lint-imports        # 分层契约
pytest -q           # 单元 + 集成 + 端到端
```

`lint-imports` 由 [`pyproject.toml`](../pyproject.toml) 的 7 条契约固化，违反直接让 CI 变红。
详见 [分层架构](architecture.md#三分层契约)。

---

## 七、可观测数据出口

| 出口 | 端点 / 配置 | 用途 |
|---|---|---|
| Trace（完整 Span 列表） | `GET /workflow/{id}/trace` | 事后复盘、Span 级排查 |
| Trace（嵌套调用树） | `GET /workflow/{id}/trace/tree` | 前端直接渲染 |
| 指标快照 | `GET /metrics` | Prometheus 侧车抓取 |
| BadCase 列表 | `GET /badcases` | 闭环迭代输入 |
| BadCase 汇总 | `GET /badcases/summary` | 看板 |
| Trace JSONL 归档 | `AIO_AGENTOS_TRACE_EXPORT_PATH` | 离线分析 |
| BadCase JSONL 归档 | `AIO_AGENTOS_BADCASE_EXPORT_PATH` | 离线分析 |

指标键名统一带 `aio_agentos_` 前缀，标签以 `{k=v}` 后缀呈现（如 `aio_agentos_tool_invocations{tool=service.restart}`）。

---

## 八、优雅停机

`Runtime.close()` 关闭存储；`IncidentWorkflowService.close()` 取消所有挂起的审批超时任务：

```python
async def close(self) -> None:
    for task in list(self._timeout_tasks.values()):
        task.cancel()
    self._timeout_tasks.clear()
```

不做这一步，进程退出时事件循环会报 `Task was destroyed but it is pending!`。

---

## 未交付

- **容器化**：仓库没有 `Dockerfile` / `docker-compose.yml`。这是部署工程，不是架构组件。
- **Kubernetes manifest / Helm chart**：同上。
- **密钥管理**：只支持环境变量，没有集成 Vault / KMS。
- **OIDC / mTLS**：`Principal` 的解析在 API 层是显式的，但真实身份提供方接入属于部署环节。
- **Prometheus 导出器**：`/metrics` 返回 JSON 快照，不是 Prometheus exposition format。
  需要一个薄适配层（`prometheus_client`）才能被直接 scrape。
