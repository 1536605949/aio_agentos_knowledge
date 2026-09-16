"""技能层：Capability 与 Skill 的契约、注册表与领域目录。"""

from skills.catalog import (
    CAPABILITIES,
    DEFAULT_INTENT,
    INCIDENT_PIPELINE,
    SKILLS,
    build_default_skill_registry,
)
from skills.models import CapabilitySpec, SkillSpec
from skills.registry import SkillRegistry

__all__ = [
    "CAPABILITIES",
    "DEFAULT_INTENT",
    "INCIDENT_PIPELINE",
    "SKILLS",
    "CapabilitySpec",
    "SkillRegistry",
    "SkillSpec",
    "build_default_skill_registry",
]
