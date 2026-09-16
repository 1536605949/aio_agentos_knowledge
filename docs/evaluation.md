# Evaluation

仓库提供评测数据结构与回归测试框架，但**不伪造“历史故障”**。生产验收应接入至少 30 条真实、脱敏的历史 case：

- 输入：Alarm + Evidence
- 期望：Root Cause + 可接受 Action
- 指标：根因准确率、动作采纳率、人工干预率、平均恢复时间、单次成本
- 流程：BadCase -> 标注 -> 回归集 -> CI 门禁

`tests/fixtures/synthetic_cases.json` 仅用于代码回归，不代表生产质量。
