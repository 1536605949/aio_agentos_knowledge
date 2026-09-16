from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RelationKind(StrEnum):
    CAUSED_BY = "CAUSED_BY"
    HAS_CAPABILITY = "HAS_CAPABILITY"
    USES_TOOL = "USES_TOOL"
    DEPENDS_ON = "DEPENDS_ON"
    LOCATED_ON = "LOCATED_ON"


class ClassDef(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    parent: str | None = None


class PropertyDef(BaseModel):
    name: str = Field(min_length=1)
    domain: str
    range: str
    required: bool = False
    description: str = ""


class RelationDef(BaseModel):
    name: str
    source_class: str
    target_class: str
    kind: RelationKind
    description: str = ""


class AxiomKind(StrEnum):
    REQUIRED_FIELD = "required_field"
    ALLOWED_VALUE = "allowed_value"
    HIGH_RISK_REQUIRES_APPROVAL = "high_risk_requires_approval"


class OntologyAxiom(BaseModel):
    name: str
    kind: AxiomKind
    field: str | None = None
    allowed_values: list[Any] = Field(default_factory=list)

    def validate(self, context: dict[str, Any]) -> tuple[bool, str | None]:
        if self.kind == AxiomKind.REQUIRED_FIELD:
            ok = bool(self.field) and self.field in context and context[self.field] is not None
            return ok, None if ok else f"required field missing: {self.field}"
        if self.kind == AxiomKind.ALLOWED_VALUE:
            # 字段缺席时不判定：公理约束的是"一旦出现必须落在词表内"，
            # 这样同一条公理既能校验推理计划，也能校验执行记录。
            if not self.field or context.get(self.field) is None:
                return True, None
            value = context[self.field]
            ok = value in self.allowed_values
            return ok, None if ok else f"{self.field}={value!r} is not allowed"
        if self.kind == AxiomKind.HIGH_RISK_REQUIRES_APPROVAL:
            risk = str(context.get("risk_level", "")).lower()
            approved = bool(context.get("approved", False))
            ok = risk not in {"high", "forbidden"} or approved
            return ok, None if ok else "high-risk action requires approval"
        return False, f"unsupported axiom kind: {self.kind}"


class OntologyVersion(BaseModel):
    version: str
    compatible_from: str | None = None
    notes: str = ""

    @field_validator("version", "compatible_from")
    @classmethod
    def semantic_version(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("version must use MAJOR.MINOR.PATCH")
        return value


class OntologyRegistry:
    """Read-only TBox registry. Runtime/ABox instances deliberately live elsewhere."""

    def __init__(
        self,
        version: OntologyVersion,
        classes: list[ClassDef] | None = None,
        properties: list[PropertyDef] | None = None,
        relations: list[RelationDef] | None = None,
        axioms: list[OntologyAxiom] | None = None,
    ) -> None:
        self.version = version
        self.classes = {x.name: x for x in classes or []}
        self.properties = {x.name: x for x in properties or []}
        self.relations = {x.name: x for x in relations or []}
        self.axioms = tuple(axioms or [])

    def validate(self, context: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for axiom in self.axioms:
            ok, reason = axiom.validate(context)
            if not ok and reason:
                errors.append(reason)
        return errors

    def is_compatible_with(self, other_version: str) -> bool:
        """判断 ``other_version`` 是否在本体声明的兼容范围内。"""
        if self.version.compatible_from is None:
            return True
        return _semver_tuple(other_version) >= _semver_tuple(self.version.compatible_from)

    def summary(self) -> dict[str, Any]:
        """面向 API / 文档的结构摘要。"""
        return {
            "version": self.version.version,
            "compatible_from": self.version.compatible_from,
            "notes": self.version.notes,
            "classes": sorted(self.classes),
            "properties": sorted(self.properties),
            "relations": sorted(self.relations),
            "axioms": [{"name": axiom.name, "kind": axiom.kind.value} for axiom in self.axioms],
            "counts": {
                "classes": len(self.classes),
                "properties": len(self.properties),
                "relations": len(self.relations),
                "axioms": len(self.axioms),
            },
        }


def _semver_tuple(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    numbers = [int(part) for part in parts[:3] if part.isdigit()]
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]
