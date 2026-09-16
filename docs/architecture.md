# 架构说明

## 正确的控制关系

```text
                    +---------------------------------------+
Alarm/API/Kafka --->| Temporal durable workflow             |
                    |                                       |
                    |  Activity: LangGraph / Reasoning      |
                    |   Log -> Reason -> Propose -> Verify  |
                    |                 |                     |
                    |                 v                     |
                    |       Governance interceptor          |
                    |                 |                     |
                    |        WAIT approval signal           |
                    |                 |                     |
                    |          governed Tool call           |
                    +---------------------------------------+

Shared read-only: Ontology
Cross-cutting: Memory / Observability
Outbound: MCP tools
Inbound: A2A AgentCard / API
```

Temporal 与 LangGraph 是**外壳/内核**，不是前后流水线。Governance 作用在每次 Tool 调用前；Ontology 是共享只读语义层；Memory/Observability 在全程工作。

## 分层依赖

- L0：`observability`
- L1：`ontology`、`memory`
- L2：`skills`、`tools`、`router`、`agents`
- L3：`governance`、`protocol`
- L4：`graph`、`temporal`、`api`

`pyproject.toml` 中用 import-linter 固化关键禁止依赖。
