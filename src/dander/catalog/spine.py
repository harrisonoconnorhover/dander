"""Canonical model metadata projected into catalog and semantic representations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dander.ingestion import SourceConfig
    from dander.transform import TransformModel, TransformProject
    from dander.transform.config import GenericTestMetadata, MetricMetadata, Scalar


@dataclass(frozen=True)
class CatalogColumn:
    """One cataloged warehouse column."""

    name: str
    data_type: str
    description: str
    nullable: bool

    def to_manifest(self) -> dict[str, object]:
        """Return the stable semantic-registry representation."""
        return {
            "name": self.name,
            "type": self.data_type,
            "description": self.description,
            "nullable": self.nullable,
        }


@dataclass(frozen=True)
class TestContract:
    """One generic assertion promised by a model's metadata."""

    kind: str
    column: str
    values: tuple[Scalar, ...] = ()
    target_relation: str | None = None
    target_field: str | None = None

    def to_manifest(self) -> dict[str, object]:
        """Return the stable semantic-registry representation."""
        manifest: dict[str, object] = {"kind": self.kind, "column": self.column}
        if self.values:
            manifest["values"] = list(self.values)
        if self.target_relation is not None:
            manifest["target_relation"] = self.target_relation
            manifest["target_field"] = self.target_field
        return manifest


@dataclass(frozen=True)
class MetricDefinition:
    """One governed business calculation attached to a model."""

    name: str
    description: str
    aggregation: str
    field: str | None
    calculation: str

    def to_manifest(self) -> dict[str, object]:
        """Return a calculation that humans and agents can inspect deterministically."""
        manifest: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            "aggregation": self.aggregation,
            "calculation": self.calculation,
        }
        if self.field is not None:
            manifest["field"] = self.field
        return manifest


@dataclass(frozen=True)
class CatalogAsset:
    """Cloud-neutral metadata spine record for one materialized model."""

    project: str
    dataset: str
    name: str
    relation: str
    description: str
    owner: str
    materialization: str
    source_system: str
    sensitivity: str
    upstream_relations: tuple[str, ...]
    columns: tuple[CatalogColumn, ...]
    tests: tuple[TestContract, ...]
    metrics: tuple[MetricDefinition, ...]

    def to_manifest(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible semantic registry record."""
        return {
            "name": self.name,
            "relation": self.relation,
            "description": self.description,
            "owner": self.owner,
            "materialization": self.materialization,
            "source_system": self.source_system,
            "sensitivity": self.sensitivity,
            "upstream_relations": list(self.upstream_relations),
            "columns": [column.to_manifest() for column in self.columns],
            "tests": [test.to_manifest() for test in self.tests],
            "metrics": [metric.to_manifest() for metric in self.metrics],
        }


class MetadataSpine:
    """Project transform metadata once into canonical catalog assets."""

    def compile(
        self,
        project: TransformProject,
        *,
        selected: Iterable[str] | None = None,
    ) -> tuple[CatalogAsset, ...]:
        """Compile selected models and their dependencies into stable assets."""
        return tuple(self._asset(project, model) for model in project.ordered(selected))

    def manifest(self, assets: Iterable[CatalogAsset]) -> dict[str, object]:
        """Build a versioned semantic registry without volatile timestamps."""
        ordered = sorted(assets, key=lambda asset: asset.relation)
        projects = sorted({asset.project for asset in ordered})
        return {
            "schema_version": 1,
            "projects": projects,
            "assets": [asset.to_manifest() for asset in ordered],
        }

    def pipeline_manifest(
        self,
        *,
        pipeline_id: str,
        source: SourceConfig,
        assets: Iterable[CatalogAsset],
    ) -> dict[str, object]:
        """Project source, model, lineage, test, and metric metadata as one snapshot."""
        ordered = sorted(assets, key=lambda asset: asset.relation)
        projects = sorted({asset.project for asset in ordered})
        project_id = projects[0] if len(projects) == 1 else ""
        return {
            "schema_version": 2,
            "pipeline_id": pipeline_id,
            "projects": projects,
            "source": {
                "name": source.name,
                "engine": str(source.engine),
                "endpoints": [
                    {
                        "name": endpoint.name,
                        "relation": (
                            f"{project_id}.raw.{source.name}_{endpoint.name}"
                            if project_id
                            else f"raw.{source.name}_{endpoint.name}"
                        ),
                        "primary_key": list(endpoint.primary_key),
                        "incremental_cursor": endpoint.incremental_cursor,
                    }
                    for endpoint in source.endpoints
                ],
            },
            "assets": [asset.to_manifest() for asset in ordered],
        }

    def _asset(self, project: TransformProject, model: TransformModel) -> CatalogAsset:
        metadata = model.metadata
        not_null_columns = {test.column for test in metadata.tests if test.not_null}
        return CatalogAsset(
            project=project.project_id,
            dataset=metadata.dataset,
            name=model.name,
            relation=_unquote(project.relation_for_model(model)),
            description=metadata.description,
            owner=metadata.owner,
            materialization=metadata.materialization.value,
            source_system=metadata.source_system,
            sensitivity=metadata.sensitivity,
            upstream_relations=tuple(
                _unquote(project.relation_for_ref(reference)) for reference in model.refs
            ),
            columns=tuple(
                CatalogColumn(
                    name=column.name,
                    data_type=column.data_type,
                    description=column.description,
                    nullable=column.name not in not_null_columns,
                )
                for column in metadata.columns
            ),
            tests=tuple(
                contract for test in metadata.tests for contract in _test_contracts(project, test)
            ),
            metrics=tuple(_metric_definition(metric) for metric in metadata.metrics),
        )


def _test_contracts(
    project: TransformProject,
    test: GenericTestMetadata,
) -> tuple[TestContract, ...]:
    contracts: list[TestContract] = []
    if test.not_null:
        contracts.append(TestContract(kind="not_null", column=test.column))
    if test.unique:
        contracts.append(TestContract(kind="unique", column=test.column))
    if test.accepted_values is not None:
        contracts.append(
            TestContract(
                kind="accepted_values",
                column=test.column,
                values=tuple(test.accepted_values),
            )
        )
    if test.relationships is not None:
        contracts.append(
            TestContract(
                kind="relationships",
                column=test.column,
                target_relation=_unquote(project.relation_for_ref(test.relationships.to)),
                target_field=test.relationships.field,
            )
        )
    return tuple(contracts)


def _unquote(relation: str) -> str:
    return relation.removeprefix("`").removesuffix("`")


def _metric_definition(metric: MetricMetadata) -> MetricDefinition:
    field = metric.field
    calculations = {
        "count": "COUNT(*)",
        "count_distinct": f"COUNT(DISTINCT `{field}`)",
        "sum": f"SUM(`{field}`)",
        "average": f"AVG(`{field}`)",
        "minimum": f"MIN(`{field}`)",
        "maximum": f"MAX(`{field}`)",
    }
    return MetricDefinition(
        name=metric.name,
        description=metric.description,
        aggregation=metric.aggregation.value,
        field=field,
        calculation=calculations[metric.aggregation.value],
    )
