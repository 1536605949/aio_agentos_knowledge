# Observability 设计

- `Span`：节点/Agent/Tool/Workflow 的输入摘要、耗时、状态和错误。
- `MetricSample`：延迟、成功率、成本等统一样本。
- `EvalResult`：case + metric + score + pass/fail。
- `Feedback`：用户/人工反馈和 BadCase 标记。

Trace 在执行路径中实时写入；异常会在 span finally 路径落盘，不能只在流程末端汇总。
