# Agent 设计

每个 Agent 必须以 `AgentSpec` 定义：

1. Capability
2. Skill
3. Input Schema
4. Output Schema
5. Tool Permission
6. Memory Policy
7. Evaluation Metric

当前实现保留六个角色，但明确边界：

- Alarm / Topology / Log：Evidence Agents，负责标准化或收集证据。
- Reasoning：唯一根因推理 Agent，消费 Evidence 输出。
- Remediation：把根因映射为“建议动作”，不绕过 Policy 直接执行高风险 Tool。
- Ticket：把结论转换为工单契约。

Tool 的真实副作用只能经 `ToolRegistry -> PolicyEngine` 执行。
