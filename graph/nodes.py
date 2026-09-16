from __future__ import annotations

from typing import Any

from ontology.models import OntologyRegistry


def supervisor_node(state: dict[str, Any], ontology: OntologyRegistry | None = None) -> dict[str, Any]:
    """Validate graph output against ontology invariants and minimum output shape."""
    result = dict(state)
    errors = list(result.get("errors", []))
    if ontology is not None:
        errors.extend(ontology.validate(result))
    if "workflow_id" not in result:
        errors.append("workflow_id missing")
    result["errors"] = errors
    return result
