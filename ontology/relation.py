"""Backward-compatible relation enum."""
from ontology.models import RelationKind

OntologyRelation = RelationKind
__all__ = ["OntologyRelation", "RelationKind"]
