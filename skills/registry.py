from __future__ import annotations

from skills.models import CapabilitySpec, SkillSpec


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: dict[str, SkillSpec] = {}
        self.capabilities: dict[str, CapabilitySpec] = {}

    def register_capability(self, spec: CapabilitySpec) -> None:
        self.capabilities[spec.name] = spec

    def register_skill(self, spec: SkillSpec) -> None:
        if spec.required_capability not in self.capabilities:
            raise ValueError(f"unknown capability: {spec.required_capability}")
        self.skills[spec.name] = spec

    def for_intent(self, intent: str) -> list[SkillSpec]:
        key = intent.strip().lower()
        return [s for s in self.skills.values() if key in {x.lower() for x in s.intents}]
