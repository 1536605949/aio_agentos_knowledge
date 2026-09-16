"""AIOps 领域的 TBox 定义。

这是"本体驱动"真正生效的地方：领域词表（根因、严重级别、动作）在此**唯一定义**，
然后被三处共同消费：

1. **Prompt 渲染** —— 把允许取值注入提示词，约束模型输出空间
2. **本体公理校验** —— ``ALLOWED_VALUE`` 公理在 Supervisor 节点拦截越界输出
3. **Agent 输出校验** —— 推理结果必须落在词表内，否则判为非法

这样"LLM 只能在业务边界内生成内容"就有了可执行的落点，
而不是停留在文档描述。
"""

from __future__ import annotations

from ontology.models import (
    AxiomKind,
    ClassDef,
    OntologyAxiom,
    OntologyRegistry,
    OntologyVersion,
    PropertyDef,
    RelationDef,
    RelationKind,
)

# --------------------------------------------------------------------- 领域词表

ROOT_CAUSES: tuple[str, ...] = (
    "upstream_timeout",
    "resource_exhaustion",
    "database_dependency_failure",
    "config_regression",
    "network_partition",
    "undetermined",
)
"""允许的根因取值。``undetermined`` 是显式的"证据不足"出口，
避免模型在无证据时被迫编造一个具体根因。"""

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "unknown")

REMEDIATION_ACTIONS: tuple[str, ...] = (
    "restart_service",
    "rollback_config",
    "scale_out",
    "escalate_to_dba",
    "escalate_to_network_team",
    "collect_more_evidence",
)

ALARM_CATEGORIES: tuple[str, ...] = (*ROOT_CAUSES, "unclassified")

# ------------------------------------------------------------------ 本体定义

_CLASSES: tuple[ClassDef, ...] = (
    ClassDef(name="Incident", description="一次故障事件，编排的最小单位"),
    ClassDef(name="Alarm", description="来自监控平台的原始告警"),
    ClassDef(name="Service", description="可被修复动作影响的服务单元"),
    ClassDef(name="Host", description="承载服务的主机或容器实例"),
    ClassDef(name="Evidence", description="支撑根因判断的证据片段"),
    ClassDef(name="RootCause", description="根因分类，取值受 ROOT_CAUSES 约束"),
    ClassDef(name="RemediationAction", description="受治理的修复动作"),
    ClassDef(name="Agent", description="执行单元，具备能力与工具权限"),
    ClassDef(name="Skill", description="面向业务意图的可路由能力契约"),
    ClassDef(name="Tool", description="具备真实副作用的最小执行单元"),
)

_PROPERTIES: tuple[PropertyDef, ...] = (
    PropertyDef(name="incidentId", domain="Incident", range="string", required=True),
    PropertyDef(name="severity", domain="Alarm", range="string", required=True),
    PropertyDef(name="serviceName", domain="Service", range="string", required=True),
    PropertyDef(name="rootCauseKind", domain="RootCause", range="string", required=True),
    PropertyDef(name="confidence", domain="RootCause", range="number", required=False),
    PropertyDef(name="actionName", domain="RemediationAction", range="string", required=True),
    PropertyDef(name="riskLevel", domain="Tool", range="string", required=True),
    PropertyDef(name="timeoutSeconds", domain="Tool", range="number", required=False),
)

_RELATIONS: tuple[RelationDef, ...] = (
    RelationDef(
        name="CAUSED_BY",
        source_class="Incident",
        target_class="RootCause",
        kind=RelationKind.CAUSED_BY,
        description="故障由某根因导致",
    ),
    RelationDef(
        name="RESOLVED_BY",
        source_class="RootCause",
        target_class="RemediationAction",
        kind=RelationKind.DEPENDS_ON,
        description="根因对应的标准处置动作",
    ),
    RelationDef(
        name="HAS_CAPABILITY",
        source_class="Agent",
        target_class="Skill",
        kind=RelationKind.HAS_CAPABILITY,
        description="Agent 具备某能力",
    ),
    RelationDef(
        name="USES_TOOL",
        source_class="Skill",
        target_class="Tool",
        kind=RelationKind.USES_TOOL,
        description="Skill 允许调用的工具白名单",
    ),
    RelationDef(
        name="LOCATED_ON",
        source_class="Service",
        target_class="Host",
        kind=RelationKind.LOCATED_ON,
        description="服务部署位置",
    ),
    RelationDef(
        name="DEPENDS_ON",
        source_class="Service",
        target_class="Service",
        kind=RelationKind.DEPENDS_ON,
        description="服务间依赖关系，用于拓扑推断",
    ),
)

_AXIOMS: tuple[OntologyAxiom, ...] = (
    OntologyAxiom(
        name="incident_id_required",
        kind=AxiomKind.REQUIRED_FIELD,
        field="workflow_id",
    ),
    OntologyAxiom(
        name="root_cause_in_vocabulary",
        kind=AxiomKind.ALLOWED_VALUE,
        field="root_cause",
        allowed_values=list(ROOT_CAUSES),
    ),
    OntologyAxiom(
        name="severity_in_vocabulary",
        kind=AxiomKind.ALLOWED_VALUE,
        field="severity",
        allowed_values=list(SEVERITIES),
    ),
    OntologyAxiom(
        name="high_risk_requires_approval",
        kind=AxiomKind.HIGH_RISK_REQUIRES_APPROVAL,
    ),
)

ACTION_TO_ROOT_CAUSE: dict[str, str] = {
    "restart_service": "upstream_timeout",
    "rollback_config": "config_regression",
    "escalate_to_dba": "database_dependency_failure",
    "escalate_to_network_team": "network_partition",
}


def build_ontology(version: str = "3.5.0", compatible_from: str | None = "3.0.0") -> OntologyRegistry:
    """构建 AIOps 领域的只读 TBox。"""
    return OntologyRegistry(
        version=OntologyVersion(
            version=version,
            compatible_from=compatible_from,
            notes="AIOps 故障根因分析与受治理修复领域的基线本体",
        ),
        classes=list(_CLASSES),
        properties=list(_PROPERTIES),
        relations=list(_RELATIONS),
        axioms=list(_AXIOMS),
    )


def root_causes_for_prompt() -> str:
    """渲染进提示词的根因白名单（逗号分隔）。"""
    return ", ".join(ROOT_CAUSES)


def actions_for_prompt() -> str:
    """渲染进提示词的动作白名单（逗号分隔）。"""
    return ", ".join(REMEDIATION_ACTIONS)


def severities_for_prompt() -> str:
    return ", ".join(SEVERITIES)


def is_valid_root_cause(value: str) -> bool:
    return value in ROOT_CAUSES


def is_valid_action(value: str) -> bool:
    return value in REMEDIATION_ACTIONS
