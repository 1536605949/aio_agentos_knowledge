# V3.5 补齐基线

本仓库以“可执行契约 + 一条真实纵向切片”为验收基线，不再用文档中出现过的名词作为“已完成”的证据。

已落地：

1. Ontology：Class / Relation / Property / Axiom / Version Schema
2. Route-Skill：Capability / Skill / Router / Tool Registry
3. Runtime：Lifecycle / AgentContext / State Ownership
4. Graph：State / Node / Supervisor / LangGraph adapter
5. Temporal：Workflow State / approval signal semantics / local runnable service / SDK adapter
6. Protocol：MCP outbound 与 A2A inbound 分离
7. Memory：Short / Long / Vector / Episodic records + store
8. Observability：Trace / Span / Metric / Evaluation / Feedback
9. Governance：Risk / RBAC / Policy interceptor / approval timeout
10. Engineering：pyproject / package init / tests / CI / Docker / import contracts

仍需由真实生产环境补充的内容：真实 Temporal 集群配置、真实 MCP Tool transport、OIDC/mTLS、PostgreSQL/VectorDB adapter、真实历史故障 golden set。
