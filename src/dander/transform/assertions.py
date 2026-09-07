"""Provider-neutral selection and reference validation for generic data assertions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dander.transform.project import TransformProjectError

if TYPE_CHECKING:
    from dander.transform.config import Scalar
    from dander.transform.project import TransformModel, TransformProject
    from dander.warehouse import RelationRef


type AssertionKind = Literal["not_null", "unique", "accepted_values", "relationships"]


@dataclass(frozen=True, slots=True)
class GenericAssertion:
    """One enabled assertion, with a stable name and resolved relationship coordinates."""

    name: str
    kind: AssertionKind
    column: str
    values: tuple[Scalar, ...] = ()
    parent: RelationRef | None = None
    parent_field: str | None = None


def plan_assertions(
    project: TransformProject, model: TransformModel
) -> tuple[GenericAssertion, ...]:
    """Select assertions in declaration order before any provider work is submitted."""
    assertions: list[GenericAssertion] = []
    for test in model.metadata.tests:
        kinds: list[AssertionKind] = []
        if test.not_null:
            kinds.append("not_null")
        if test.unique:
            kinds.append("unique")
        if test.accepted_values is not None:
            kinds.append("accepted_values")
        parent = None
        parent_field = None
        if test.relationships is not None:
            relationship = test.relationships
            if relationship.to in project.models:
                parent_model = project.models[relationship.to]
                if relationship.field not in {
                    column.name for column in parent_model.metadata.columns
                }:
                    raise TransformProjectError(
                        "Relationship test references an undeclared parent column: "
                        f"{relationship.to}.{relationship.field}"
                    )
            parent = project.relation_ref_for_ref(relationship.to)
            parent_field = relationship.field
            kinds.append("relationships")
        for kind in kinds:
            assertions.append(
                GenericAssertion(
                    name=f"{model.name}.{test.column}.{kind}",
                    kind=kind,
                    column=test.column,
                    values=tuple(test.accepted_values or ()) if kind == "accepted_values" else (),
                    parent=parent if kind == "relationships" else None,
                    parent_field=parent_field if kind == "relationships" else None,
                )
            )
    return tuple(assertions)
