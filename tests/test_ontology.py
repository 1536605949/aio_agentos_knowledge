import pytest

from ontology import AxiomKind, ClassDef, OntologyAxiom, OntologyRegistry, OntologyVersion


def test_semver_and_axiom_validation():
    registry = OntologyRegistry(
        version=OntologyVersion(version="3.5.0"),
        classes=[ClassDef(name="Alarm")],
        axioms=[OntologyAxiom(name="wf", kind=AxiomKind.REQUIRED_FIELD, field="workflow_id")],
    )
    assert registry.validate({"workflow_id": "wf-1"}) == []
    assert registry.validate({}) == ["required field missing: workflow_id"]


def test_invalid_semver():
    with pytest.raises(ValueError):
        OntologyVersion(version="3.5")
