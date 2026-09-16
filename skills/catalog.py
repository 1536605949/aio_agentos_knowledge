"""AIOps 领域的 Capability / Skill 目录。

这是 Route-Skill 路由的**静态事实源**：``intent -> skill -> capability -> agent -> tool 白名单``。

设计意图：让"业务意图"成为系统的第一等输入。调用方只需要说"我要诊断"，不需要知道
背后由哪个 Agent、调用哪些工具。Skill 同时是**权限边界**——Agent 只能使用
``allowed_tools`` 里声明的工具，这条约束在治理层可被复核。

原实现只提供了 :class:`skills.registry.SkillRegistry` 这个空容器，
没有任何注册内容，导致 ``router/`` 整条链路运行时零引用。本模块补齐注册内容。
"""

from __future__ import annotations

from governance.models import RiskLevel
from skills.models import CapabilitySpec, SkillSpec
from skills.registry import SkillRegistry

CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(name="alarm_normalization", description="把原始告警归一化为统一结构"),
    CapabilitySpec(name="topology_evidence", description="采集服务拓扑与依赖证据"),
    CapabilitySpec(name="log_evidence", description="采集并结构化日志证据"),
    CapabilitySpec(name="root_cause_reasoning", description="基于证据推断故障根因"),
    CapabilitySpec(name="remediation", description="基于根因规划受治理的修复动作"),
    CapabilitySpec(name="ticketing", description="创建工单并升级到人工团队"),
)

SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="normalize_alarm",
        description="告警标准化：统一 severity 与分类，供下游推理使用",
        intents=["alarm", "normalize_alarm", "alert", "告警", "标准化"],
        required_capability="alarm_normalization",
        allowed_agents=["alarm"],
        allowed_tools=[],
        risk_level=RiskLevel.LOW,
    ),
    SkillSpec(
        name="collect_topology",
        description="拓扑取证：查询服务依赖，为根因推断提供上下文",
        intents=["topology", "dependency", "拓扑", "依赖"],
        required_capability="topology_evidence",
        allowed_agents=["topology"],
        allowed_tools=["topology.lookup"],
        risk_level=RiskLevel.LOW,
    ),
    SkillSpec(
        name="collect_logs",
        description="日志取证：检索并结构化错误日志",
        intents=["logs", "log_evidence", "日志", "取证"],
        required_capability="log_evidence",
        allowed_agents=["log"],
        allowed_tools=["logs.search"],
        risk_level=RiskLevel.LOW,
    ),
    SkillSpec(
        name="infer_root_cause",
        description="根因推理：在受本体词表约束的取值空间内给出根因与置信度",
        intents=["diagnose", "root_cause", "reason", "根因", "诊断"],
        required_capability="root_cause_reasoning",
        allowed_agents=["reasoning"],
        allowed_tools=[],
        risk_level=RiskLevel.LOW,
    ),
    SkillSpec(
        name="remediate_incident",
        description="受治理修复：从本体词表选择动作，高风险动作必须人工审批",
        intents=["remediate", "fix", "repair", "修复", "恢复"],
        required_capability="remediation",
        allowed_agents=["remediation"],
        allowed_tools=["service.restart", "config.rollback", "service.scale_out"],
        risk_level=RiskLevel.HIGH,
    ),
    SkillSpec(
        name="create_ticket",
        description="工单升级：把无法自动修复的故障转人工",
        intents=["ticket", "escalate", "工单", "升级"],
        required_capability="ticketing",
        allowed_agents=["ticket"],
        allowed_tools=["ticket.create"],
        risk_level=RiskLevel.LOW,
    ),
)

DEFAULT_INTENT = "diagnose"
"""未指定意图时的默认入口。"""

INCIDENT_PIPELINE: tuple[str, ...] = (
    "normalize_alarm",
    "collect_logs",
    "infer_root_cause",
    "remediate_incident",
)
"""端到端处理一次故障所需的技能链（用于 ``/router/plan`` 展示）。"""


def build_default_skill_registry() -> SkillRegistry:
    """构建包含全部 AIOps 能力的技能注册表。"""
    registry = SkillRegistry()
    for capability in CAPABILITIES:
        registry.register_capability(capability)
    for skill in SKILLS:
        registry.register_skill(skill)
    return registry


__all__ = [
    "CAPABILITIES",
    "DEFAULT_INTENT",
    "INCIDENT_PIPELINE",
    "SKILLS",
    "build_default_skill_registry",
]
