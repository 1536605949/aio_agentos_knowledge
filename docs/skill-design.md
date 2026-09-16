# Skill / Capability / Router

- Capability：稳定的“能做什么”，例如 `root_cause_reasoning`。
- Skill：面向业务意图的可路由能力，绑定 Capability、Agent 白名单、Tool 白名单、风险等级和 Workflow Template。
- Router：执行 `Intent -> Skill -> Capability -> Agent -> Tool -> Workflow`。

Capability 不再与 Skill 同义：Capability 是能力分类，Skill 是可版本化、可治理、可路由的业务契约。
