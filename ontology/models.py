from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RelationKind(str, Enum):
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


class AxiomKind(str, Enum):
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
            value = context.get(self.field or "")
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
