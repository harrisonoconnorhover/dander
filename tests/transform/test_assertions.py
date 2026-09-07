"""Shared generic assertion selection and reference resolution, without provider SDKs."""

from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest

from dander.transform.assertions import plan_assertions
from dander.transform.config import (
    ColumnMetadata,
    GenericTestMetadata,
    ModelMetadata,
    RelationshipMetadata,
)
from dander.transform.project import TransformModel, TransformProject, TransformProjectError


def _model(name: str, tests: list[GenericTestMetadata]) -> TransformModel:
    return TransformModel(
        metadata=ModelMetadata(
            model=name,
            description="Synthetic model.",
            owner="test",
            source_system="fixture",
            sensitivity="public",
            columns=[ColumnMetadata(name="id", data_type="STRING", description="Identifier.")],
            tests=tests,
        ),
        sql="SELECT 1 AS id",
        refs=(),
        sql_path=Path(f"{name}.sql"),
    )


@pytest.mark.parametrize(
    "enabled", [flags for flags in product((False, True), repeat=4) if any(flags)]
)
def test_enabled_rules_keep_names_order_values_and_resolved_parent(
    enabled: tuple[bool, ...],
) -> None:
    test = GenericTestMetadata(
        column="id",
        not_null=enabled[0],
        unique=enabled[1],
        accepted_values=["O'Reilly", 7, True] if enabled[2] else None,
        relationships=RelationshipMetadata(to="raw_parent", field="id") if enabled[3] else None,
    )
    model = _model("child", [test])
    project = TransformProject(catalog="warehouse", target_dialect="postgres", models=[model])

    planned = plan_assertions(project, model)

    kinds = [
        kind
        for kind, active in zip(
            ("not_null", "unique", "accepted_values", "relationships"), enabled, strict=True
        )
        if active
    ]
    assert [assertion.kind for assertion in planned] == kinds
    assert [assertion.name for assertion in planned] == [f"child.id.{kind}" for kind in kinds]
    for assertion in planned:
        assert assertion.column == "id"
        assert assertion.values == (
            ("O'Reilly", 7, True) if assertion.kind == "accepted_values" else ()
        )
        if assertion.kind == "relationships":
            assert assertion.parent is not None
            assert assertion.parent.coordinates == ("warehouse", "raw", "parent")
            assert assertion.parent_field == "id"
        else:
            assert assertion.parent is None


@pytest.mark.parametrize(
    "parent_name,field,error",
    [
        ("parent", "id", None),
        ("parent", "missing", "undeclared parent column"),
        ("missing", "id", "Unknown model reference"),
    ],
)
def test_model_relationships_validate_the_declared_parent(
    parent_name: str, field: str, error: str | None
) -> None:
    child = _model(
        "child",
        [
            GenericTestMetadata(
                column="id", relationships=RelationshipMetadata(to=parent_name, field=field)
            )
        ],
    )
    parent = _model("parent", [])
    project = TransformProject(
        catalog="warehouse", target_dialect="postgres", models=[child, parent]
    )
    if error is not None:
        with pytest.raises(TransformProjectError, match=error):
            plan_assertions(project, child)
    else:
        (assertion,) = plan_assertions(project, child)
        assert assertion.parent == project.relation_ref_for_model(parent)


def test_planning_keeps_declaration_order_and_empty_models() -> None:
    model = _model(
        "child",
        [
            GenericTestMetadata(column="id", unique=True),
            GenericTestMetadata(column="id", not_null=True),
        ],
    )
    empty = _model("empty", [])
    project = TransformProject(
        catalog="warehouse", target_dialect="postgres", models=[model, empty]
    )
    assert [item.name for item in plan_assertions(project, model)] == [
        "child.id.unique",
        "child.id.not_null",
    ]
    assert plan_assertions(project, empty) == ()
