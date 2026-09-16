# Memory 设计

- Short：当前任务临时上下文。
- Long：长期业务经验。
- Vector：可检索历史知识的向量引用。
- Episodic：一次故障从输入、推理到结果的事件记录。

Memory 不是 Workflow 状态源。审批、执行状态、幂等状态不能靠 Memory 恢复；这些字段由持久化 Workflow 拥有。
