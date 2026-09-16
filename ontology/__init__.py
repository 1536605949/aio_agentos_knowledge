"""本体层：只读 TBox 定义与领域词表。

对外契约：
- :class:`OntologyRegistry` —— 只读 TBox 注册表，供 Supervisor 与 API 消费
- :func:`build_ontology` —— 构建 AIOps 领域基线本体
- 领域词表常量（``ROOT_CAUSES`` / ``SEVERITIES`` / ``REMEDIATION_ACTIONS``）——
  被 Prompt 渲染、公理校验与 Agent 输出校验共同消费，是"本体驱动"的单一事实源

ABox（运行实例：告警、设备、执行记录）**不进入本体**，由业务数据承载。
"""

from ontology.domain import (
    ACTION_TO_ROOT_CAUSE,
    ALARM_CATEGORIES,
    REMEDIATION_ACTIONS,
    ROOT_CAUSES,
    SEVERITIES,
    actions_for_prompt,
    build_ontology,
    is_valid_action,
    is_valid_root_cause,
    root_causes_for_prompt,
    severities_for_prompt,
)
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

__all__ = [
    "ACTION_TO_ROOT_CAUSE",
    "ALARM_CATEGORIES",
    "REMEDIATION_ACTIONS",
    "ROOT_CAUSES",
    "SEVERITIES",
    "AxiomKind",
    "ClassDef",
    "OntologyAxiom",
    "OntologyRegistry",
    "OntologyVersion",
    "PropertyDef",
    "RelationDef",
    "RelationKind",
    "actions_for_prompt",
    "build_ontology",
    "is_valid_action",
    "is_valid_root_cause",
    "root_causes_for_prompt",
    "severities_for_prompt",
]
