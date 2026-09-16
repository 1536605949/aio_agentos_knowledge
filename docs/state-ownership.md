# 状态所有权

状态同步遵循“一个字段只有一个持久化事实源”。禁止 AgentContext、Memory 或 Graph State 反向覆盖 Workflow 的持久化字段。

| 字段/数据 | 唯一事实源 | 写权限 | 生命周期 | 同步时机 |
|---|---|---|---|---|
| `workflow_id` / Workflow status | `WorkflowState` | Temporal/Workflow | 小时～天 | Workflow event/signal |
| `root_cause` / `confidence` | `WorkflowState` | Workflow 接收 reasoning Activity 结果后 | Workflow | Activity 完成 |
| `proposed_action` / `approved` | `WorkflowState` | Workflow；approval 仅由 Signal/API 写入 | Workflow | 建议/审批事件 |
| Agent 输入输出 | `AgentContext` | 当前 Agent | 单次 Agent 调用 | 调用开始/结束 |
| Graph 中间 evidence | `IncidentGraphState` | 当前 Graph node | 单次图执行 | node transition |
| Short Memory | Memory store | Memory service | 当前任务 | 明确 checkpoint |
| Long/Vector Memory | Memory store | Memory service | 长期 | 明确写入事件 |
| Incident episode | Episodic Memory | Workflow completion handler | 长期 | completed/rejected/timed_out |
| Trace/Span | Trace backend | Observability instrumentation | 全程 | 每个 node/tool/workflow span |

## 冲突处理

1. Workflow 重放时以 Workflow history/`WorkflowState` 为准，不读取 Memory 来重建审批或执行状态。
2. Memory 是派生/检索数据，不是编排状态数据库。
3. AgentContext 不跨 Agent 调用复用。
4. Trace 只用于诊断，不参与业务状态判定。
