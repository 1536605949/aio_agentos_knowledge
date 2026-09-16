"""路由 / 技能 / 本体 / Agent 的集成测试。

重点验证三件事：

1. 技能目录注册完整，且**每个 Skill 声明的 capability 都有 Agent 承接**（不再有孤立组件）。
2. 本体词表是单一事实源：Prompt 渲染、公理校验、Agent 输出校验三处一致。
3. 审批门由工具声明的风险等级驱动，而不是启发式判断。
"""

from __future__ import annotations

import pytest

from agents import AgentRegistry, build_agents
from agents.implementations import ReasoningAgent
from governance.models import Principal, RiskLevel
from observability import AgentTrace
from ontology import (
    ROOT_CAUSES,
    SEVERITIES,
    AxiomKind,
    OntologyAxiom,
    OntologyRegistry,
    OntologyVersion,
    build_ontology,
    is_valid_action,
    is_valid_root_cause,
    root_causes_for_prompt,
)
from router import RouteSkillRouter
from runtime import AgentContext
from skills import CAPABILITIES, INCIDENT_PIPELINE, SKILLS, build_default_skill_registry
from tools import ACTION_RISK, ACTION_TO_TOOL, build_default_tool_registry

# ------------------------------------------------------------------ 技能目录

def test_skill_registry_is_fully_registered():
    registry = build_default_skill_registry()
    assert sorted(registry.skills) == sorted(spec.name for spec in SKILLS)
    assert sorted(registry.capabilities) == sorted(spec.name for spec in CAPABILITIES)


def test_every_skill_capability_has_an_agent():
    """孤立组件检查：Skill 声明的 capability 必须真的有 Agent 承接。"""
    registry = build_default_skill_registry()
    agents = AgentRegistry(build_agents())
    capabilities = agents.capabilities()

    for skill in registry.skills.values():
        assert skill.required_capability in capabilities, f"{skill.name} 的 capability 无人承接"
        for agent_name in skill.allowed_agents:
            assert agent_name in agents.names(), f"{skill.name} 指向了不存在的 Agent: {agent_name}"


def test_every_skill_tool_is_registered():
    """Skill 允许调用的工具必须真的在工具注册表里。"""
    skills = build_default_skill_registry()
    tools = build_default_tool_registry()
    for skill in skills.skills.values():
        for tool in skill.allowed_tools:
            assert tools.has(tool), f"{skill.name} 引用了未注册的工具: {tool}"


def test_agent_tool_permissions_are_registered():
    agents = AgentRegistry(build_agents())
    tools = build_default_tool_registry()
    for spec in agents.specs():
        for tool in spec.tool_permissions:
            assert tools.has(tool), f"{spec.name} 声明了未注册的工具: {tool}"


# -------------------------------------------------------------------- 路由

def test_router_resolves_all_documented_intents():
    router = RouteSkillRouter(build_default_skill_registry())
    for intent in router.intents():
        decision = router.route(intent)
        assert decision.agent
        assert decision.capability


def test_router_plan_covers_the_incident_pipeline():
    router = RouteSkillRouter(build_default_skill_registry())
    plan = router.plan()
    assert [decision.skill for decision in plan] == list(INCIDENT_PIPELINE)
    assert all(decision.workflow_template == "incident_response" for decision in plan)


def test_router_falls_back_to_default_intent():
    router = RouteSkillRouter(build_default_skill_registry())
    decision = router.route_or_default("完全不存在的意图")
    assert decision.skill == "infer_root_cause"


def test_router_raises_for_unknown_intent():
    router = RouteSkillRouter(build_default_skill_registry())
    with pytest.raises(LookupError):
        router.route("nope")


# -------------------------------------------------------------------- 本体

def test_ontology_vocabulary_is_single_source_of_truth():
    """Prompt 渲染出的白名单必须与词表常量完全一致。"""
    rendered = root_causes_for_prompt()
    for cause in ROOT_CAUSES:
        assert cause in rendered
    assert is_valid_root_cause("upstream_timeout") is True
    assert is_valid_root_cause("made_up_cause") is False
    assert is_valid_action("restart_service") is True
    assert is_valid_action("reboot_everything") is False


def test_ontology_summary_exposes_tbox():
    summary = build_ontology().summary()
    assert summary["version"] == "3.5.0"
    assert summary["counts"]["classes"] == 10
    assert summary["counts"]["axioms"] == 4
    assert "Incident" in summary["classes"]
    assert "CAUSED_BY" in summary["relations"]


def test_ontology_version_compatibility():
    ontology = build_ontology(version="3.5.0", compatible_from="3.0.0")
    assert ontology.is_compatible_with("3.4.1") is True
    assert ontology.is_compatible_with("3.0.0") is True
    assert ontology.is_compatible_with("2.9.0") is False


def test_allowed_value_axiom_skips_absent_fields():
    ontology = OntologyRegistry(
        version=OntologyVersion(version="1.0.0"),
        axioms=[
            OntologyAxiom(
                name="root_cause_in_vocabulary",
                kind=AxiomKind.ALLOWED_VALUE,
                field="root_cause",
                allowed_values=list(ROOT_CAUSES),
            )
        ],
    )
    assert ontology.validate({}) == []
    assert ontology.validate({"root_cause": "upstream_timeout"}) == []
    assert ontology.validate({"root_cause": "made_up"}) == ["root_cause='made_up' is not allowed"]


def test_high_risk_axiom_requires_approval():
    ontology = OntologyRegistry(
        version=OntologyVersion(version="1.0.0"),
        axioms=[OntologyAxiom(name="high_risk", kind=AxiomKind.HIGH_RISK_REQUIRES_APPROVAL)],
    )
    assert ontology.validate({"risk_level": "high", "approved": False}) == [
        "high-risk action requires approval"
    ]
    assert ontology.validate({"risk_level": "high", "approved": True}) == []
    assert ontology.validate({"risk_level": "low"}) == []


def test_severity_axiom_rejects_unknown_severity():
    ontology = build_ontology()
    errors = ontology.validate({"workflow_id": "w1", "root_cause": "upstream_timeout", "severity": "bogus"})
    assert errors == ["severity='bogus' is not allowed"]
    assert all(severity in SEVERITIES for severity in SEVERITIES)


# ------------------------------------------------------------------- Agent

async def test_reasoning_agent_coerces_out_of_vocabulary_output():
    """模型给出词表外的根因时，Agent 必须收敛并打上标记。"""

    class _RogueLLM:
        provider = "rogue"
        model = "rogue-1"

        async def complete(self, request):
            from llm import LLMResponse, LLMUsage

            return LLMResponse(
                text='{"root_cause": "cosmic_rays", "confidence": 0.99, "rationale": "trust me"}',
                provider=self.provider,
                model=self.model,
                usage=LLMUsage(total_tokens=10),
            )

    agent = ReasoningAgent(llm=_RogueLLM())
    ctx = AgentContext(workflow_id="w1", trace_id="t1", agent_name="reasoning", inputs={"evidence": []})
    output = await agent.run(ctx, AgentTrace("t1"))

    assert output["root_cause"] == "undetermined"
    assert output["ontology_violation"] is True
    assert output["confidence"] <= 0.3
    assert output["raw_root_cause"] == "cosmic_rays"


async def test_agent_output_schema_is_enforced():
    from agents import AgentOutputError, BaseAgent
    from agents.specs import AgentSpec

    class BadAgent(BaseAgent):
        spec = AgentSpec(
            name="bad",
            capabilities=["x"],
            skills=[],
            input_schema={},
            output_schema={"type": "object", "properties": {"required_field": {"type": "string"}}},
        )

        async def execute(self, context, trace):
            return {"something_else": 1}

    ctx = AgentContext(workflow_id="w", trace_id="t", agent_name="bad", inputs={})
    with pytest.raises(AgentOutputError):
        await BadAgent().run(ctx, AgentTrace("t"))


async def test_lifecycle_moves_to_completed():
    agent = ReasoningAgent()
    ctx = AgentContext(workflow_id="w", trace_id="t", agent_name="reasoning", inputs={"evidence": []})
    await agent.run(ctx, AgentTrace("t"))
    assert ctx.lifecycle.value == "completed"


# --------------------------------------------------- 审批门由工具风险驱动

def test_action_to_tool_mapping_matches_tool_risk():
    tools = build_default_tool_registry()
    assert ACTION_TO_TOOL["restart_service"] == "service.restart"
    assert "collect_more_evidence" not in ACTION_TO_TOOL  # 无副作用动作不该绑定工具

    for action, tool in ACTION_TO_TOOL.items():
        assert ACTION_RISK[action] == tools.get(tool).risk_level

    assert tools.get("service.restart").risk_level is RiskLevel.HIGH
    assert tools.get("service.scale_out").risk_level is RiskLevel.MEDIUM
    assert tools.get("topology.lookup").risk_level is RiskLevel.LOW


async def test_remediation_agent_derives_approval_from_tool_risk():
    from agents.implementations import RemediationAgent

    tools = build_default_tool_registry()
    agent = RemediationAgent(tools=tools, principal=Principal(subject="a", roles={"operator"}))

    ctx = AgentContext(
        workflow_id="w",
        trace_id="t",
        agent_name="remediation",
        inputs={"root_cause": "upstream_timeout", "confidence": 0.8},
    )
    output = await agent.run(ctx, AgentTrace("t"))
    assert output["action"] == "restart_service"
    assert output["requires_approval"] is True  # 来自 service.restart 的 HIGH 风险声明

    ctx2 = AgentContext(
        workflow_id="w",
        trace_id="t",
        agent_name="remediation",
        inputs={"root_cause": "database_dependency_failure", "confidence": 0.8},
    )
    output2 = await agent.run(ctx2, AgentTrace("t"))
    assert output2["action"] == "escalate_to_dba"
    assert output2["requires_approval"] is False
