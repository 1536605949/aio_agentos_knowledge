# 设计文档索引

本目录记录 **AIO-AgentOS v3.5** 的设计决策、契约边界与运行方式。

> **所有描述都以当前代码为准**——如果文档与代码不一致，以代码为事实，并请提交修正。
> 判断"某个能力是否真的存在"的最终依据是：
> `aio-agentos check` 是否通过、`pytest` 是否有对应用例。

---

## 建议阅读顺序

**第一次接触这个项目**

1. [实现状态总览](implementation-status.md) —— 哪些做完了、哪些是边界
2. [分层架构](architecture.md) —— 模块职责与依赖方向
3. [状态所有权](state-ownership.md) —— 改代码前必读的三条红线

**想理解"本体驱动"到底怎么生效**

4. [本体设计](ontology-design.md) —— 词表如何同时约束提示词、公理与输出
5. [Agent 设计](agent-design.md) —— 6 个 Agent 的职责、生命周期与依赖注入
6. [LLM 能力层](llm-design.md) —— 协议、提示词治理、provider 与降级

**想理解治理与安全**

7. [工具与治理](security-design.md) —— 风险分级、RBAC、职责分离、10 步调用链
8. [弹性与横切能力](resilience-design.md) —— 限流、缓存、资源锁、熔断

**想接入或部署**

9. [运行与配置](operations.md) —— 环境变量全表、CLI、三种部署形态
10. [API 参考](api-spec.md) —— 20 个端点的完整契约

**想理解可观测与迭代闭环**

11. [可观测性与闭环](observability-design.md) —— Trace 树、指标、BadCase
12. [评估](evaluation.md) —— 测试策略、回归基线、质量门禁
13. [记忆与持久化](memory-design.md) —— 四类记忆 + `DocumentStore`
14. [技能与路由](skill-design.md) —— 意图如何变成可审计的执行契约

---

## 全部文档

| 文档 | 主题 | 回答的关键问题 |
|---|---|---|
| [implementation-status.md](implementation-status.md) | 实现状态 | 每个模块做到什么程度？哪些是明确不做的？ |
| [architecture.md](architecture.md) | 分层架构 | 谁依赖谁？契约如何固化？依赖在哪里装配？ |
| [state-ownership.md](state-ownership.md) | 状态所有权 | 三套状态各归谁？为什么审批状态必须持久化？ |
| [ontology-design.md](ontology-design.md) | 本体驱动 | 领域词表如何同时约束提示词、公理与输出？ |
| [agent-design.md](agent-design.md) | Agent | 6 个 Agent 的职责、生命周期与依赖注入 |
| [llm-design.md](llm-design.md) | LLM 能力层 | 如何换模型不改业务代码？提示词如何被治理？ |
| [skill-design.md](skill-design.md) | 技能与路由 | 意图如何变成可审计的执行契约？ |
| [security-design.md](security-design.md) | 工具与治理 | 高风险动作如何保证不会自动执行？ |
| [memory-design.md](memory-design.md) | 记忆与持久化 | 四类记忆的区别？状态如何跨重启保留？ |
| [observability-design.md](observability-design.md) | 可观测与闭环 | Trace 为什么是树？BadCase 从哪里来、到哪里去？ |
| [resilience-design.md](resilience-design.md) | 弹性 | 限流、缓存、资源锁、熔断在调用链的哪一步？ |
| [api-spec.md](api-spec.md) | API | 20 个端点的请求/响应契约与错误语义 |
| [operations.md](operations.md) | 运行与配置 | 所有环境变量、CLI 命令、三种部署形态 |
| [evaluation.md](evaluation.md) | 评估 | 测试策略、15 项缺陷回归基线、质量门禁 |
| [knowledge-flow.html](knowledge-flow.html) | 知识流 | 交互式控制拓扑图（浏览器打开） |
| [history/](history/) | 历史记录 | 两轮架构审查与面试陈述审计的原始结论 |

---

## 历史记录说明

`history/` 下的两份文档是**审计快照**，记录的是当时（修复前）的真实状态，
保留原文以便追溯"问题是什么、怎么发现的"：

- `history/02_architecture_review_round2.md` —— 第二轮全面架构审查，识别 N1–N22 共 22 项问题
- `history/03_interview_claim_audit.md` —— 面试陈述 vs 实现逐条核验（当时 1 成立 / 4 部分 / 10 不成立）

**这些结论已被本轮修复大幅改变**，当前状态请看 [implementation-status.md](implementation-status.md)。

---

## 文档维护约定

写这份文档集时遵循四条规则：

1. **给出代码位置**：描述设计意图时，必须同时给出文件路径 + 关键符号。
2. **可验证**：描述"已实现"时，必须能在 `aio-agentos check` 或 `pytest` 中找到对应验证。
3. **不写无落点的承诺**：不写"未来会支持 XX"。
   边界与未交付项统一写在每篇文末的「未交付」一节，以及
   [implementation-status.md](implementation-status.md) 的"明确未交付"。
4. **诚实标注**：`◐ 参考实现` 表示逻辑完整但外部依赖以无副作用的参考实现代替；
   测试用例数、文件数等数值以实际命令输出为准。

---

## 一键校验文档与代码的一致性

```bash
ruff check .        # All checks passed
lint-imports        # 7 kept, 0 broken
pytest -q           # 133 passed
aio-agentos check   # 自检通过
```

文档中出现的每个"已验证"结论都能用上面四条命令复现。
