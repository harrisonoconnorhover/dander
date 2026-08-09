"""Model discovery, dependency resolution, and safe warehouse SQL compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot
from jinja2 import Environment, StrictUndefined, TemplateError
from sqlglot import exp

from dander.transform.config import ModelMetadata, TransformConfigError, load_model_metadata
from dander.transform.dialects import PortableSqlError, parse_portable_query, render_portable_query
from dander.transform.model import SqlDialect, parse_refs
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_POSTGRESQL_CATALOG = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RAW_PREFIX = "raw_"


class TransformProjectError(ValueError):
    """Raised before execution when a transform project cannot be resolved safely."""


@dataclass(frozen=True)
class TransformModel:
    """A SQL model paired with its validated metadata and dependency declarations."""

    metadata: ModelMetadata
    sql: str
    refs: tuple[str, ...]
    sql_path: Path

    @property
    def name(self) -> str:
        """Return the canonical model name."""
        return self.metadata.model


class TransformProject:
    """Discovered transform models and their catalog-scoped relation resolver."""

    def __init__(
        self,
        *,
        catalog: str | None = None,
        project_id: str | None = None,
        raw_namespace: str = "raw",
        models: Iterable[TransformModel],
        target_dialect: SqlDialect | str = SqlDialect.BIGQUERY,
    ) -> None:
        if catalog is None:
            catalog = project_id
        elif project_id is not None and project_id != catalog:
            raise TransformProjectError("catalog and legacy project_id must match")
        if catalog is None:
            raise TransformProjectError("Warehouse catalog is required")
        try:
            self.target_dialect = SqlDialect(target_dialect)
        except ValueError as error:
            raise TransformProjectError(f"Unknown target SQL dialect: {target_dialect}") from error
        if self.target_dialect is SqlDialect.BIGQUERY:
            if not _PROJECT_ID.fullmatch(catalog):
                raise TransformProjectError("Invalid GCP project id")
        elif self.target_dialect is SqlDialect.POSTGRES:
            if not _POSTGRESQL_CATALOG.fullmatch(catalog):
                raise TransformProjectError("Invalid PostgreSQL database name")
        elif not _IDENTIFIER.fullmatch(catalog):
            raise TransformProjectError("Invalid warehouse catalog identifier")
        if not _IDENTIFIER.fullmatch(raw_namespace):
            raise TransformProjectError("Invalid raw namespace identifier")
        indexed: dict[str, TransformModel] = {}
        for model in models:
            if model.name in indexed:
                raise TransformProjectError(f"Duplicate model name: {model.name}")
            indexed[model.name] = model
        if not indexed:
            raise TransformProjectError("No SQL models were found")
        self.catalog = catalog
        self.raw_namespace = raw_namespace
        self.models = indexed
        self._validate_references()

    @property
    def project_id(self) -> str:
        """Return the legacy BigQuery-named alias for the warehouse catalog."""
        return self.catalog

    @classmethod
    def load(
        cls,
        models_dir: Path,
        *,
        catalog: str | None = None,
        project_id: str | None = None,
        raw_namespace: str = "raw",
        target_dialect: SqlDialect | str = SqlDialect.BIGQUERY,
    ) -> TransformProject:
        """Discover every SQL model beneath a directory and load its YAML sidecar.

        Args:
            models_dir: Root containing model SQL and YAML files.
            catalog: Warehouse catalog used to qualify or validate compiled relations.
            project_id: Deprecated compatibility alias for ``catalog``.

        Returns:
            A validated transform project.

        Raises:
            TransformProjectError: If discovery, metadata, or model naming is invalid.
        """
        discovered: list[TransformModel] = []
        for sql_path in sorted(models_dir.rglob("*.sql")):
            sidecar = sql_path.with_suffix(".yml")
            if not sidecar.exists():
                alternate = sql_path.with_suffix(".yaml")
                sidecar = alternate if alternate.exists() else sidecar
            if not sidecar.exists():
                raise TransformProjectError(f"Missing YAML sidecar for model: {sql_path}")
            try:
                metadata = load_model_metadata(sidecar)
                sql = sql_path.read_text()
            except (OSError, TransformConfigError) as error:
                raise TransformProjectError(str(error)) from error
            if metadata.model != sql_path.stem:
                raise TransformProjectError(
                    f"Model name {metadata.model!r} does not match SQL file {sql_path.name!r}"
                )
            discovered.append(
                TransformModel(
                    metadata=metadata,
                    sql=sql,
                    refs=tuple(parse_refs(sql)),
                    sql_path=sql_path,
                )
            )
        return cls(
            catalog=catalog,
            project_id=project_id,
            raw_namespace=raw_namespace,
            models=discovered,
            target_dialect=target_dialect,
        )

    def ordered(self, selected: Iterable[str] | None = None) -> tuple[TransformModel, ...]:
        """Return selected models plus model dependencies in topological order.

        Args:
            selected: Optional model names. Raw relations are dependencies but not build targets.

        Returns:
            Models ordered so every model dependency precedes its consumer.

        Raises:
            TransformProjectError: If selection is unknown or the model graph has a cycle.
        """
        roots = tuple(selected) if selected is not None else tuple(sorted(self.models))
        if unknown := sorted(set(roots) - self.models.keys()):
            raise TransformProjectError(f"Unknown selected models: {', '.join(unknown)}")

        state: dict[str, int] = {}
        ordered: list[TransformModel] = []
        stack: list[str] = []

        def visit(name: str) -> None:
            marker = state.get(name, 0)
            if marker == 2:
                return
            if marker == 1:
                start = stack.index(name)
                cycle = [*stack[start:], name]
                raise TransformProjectError(f"Model dependency cycle: {' -> '.join(cycle)}")
            state[name] = 1
            stack.append(name)
            model = self.models[name]
            for reference in model.refs:
                if reference in self.models:
                    visit(reference)
            stack.pop()
            state[name] = 2
            ordered.append(model)

        for root in roots:
            visit(root)
        return tuple(ordered)

    def relation_for_model(
        self,
        model: TransformModel,
        *,
        target_dialect: SqlDialect | str | None = None,
    ) -> str:
        """Return a quoted fully-qualified output relation."""
        dialect = self._resolved_target(target_dialect)
        return self._render_relation(self.relation_ref_for_model(model), dialect=dialect)

    def relation_ref_for_model(self, model: TransformModel) -> RelationRef:
        """Return canonical coordinates for one materialized model."""
        return RelationRef(
            catalog=self.catalog,
            namespace=model.metadata.namespace,
            name=model.name,
        )

    def relation_for_ref(
        self,
        reference: str,
        *,
        target_dialect: SqlDialect | str | None = None,
    ) -> str:
        """Resolve a model reference or conventional `raw_<table>` source reference."""
        dialect = self._resolved_target(target_dialect)
        return self._render_relation(self.relation_ref_for_ref(reference), dialect=dialect)

    def relation_ref_for_ref(self, reference: str) -> RelationRef:
        """Resolve a model or raw reference without provider-specific rendering."""
        if reference in self.models:
            return self.relation_ref_for_model(self.models[reference])
        if reference.startswith(_RAW_PREFIX):
            table = reference.removeprefix(_RAW_PREFIX)
            if _IDENTIFIER.fullmatch(table):
                return RelationRef(
                    catalog=self.catalog,
                    namespace=self.raw_namespace,
                    name=table,
                )
        raise TransformProjectError(f"Unknown model reference: {reference}")

    def compile(
        self,
        model: TransformModel,
        *,
        target_dialect: SqlDialect | str | None = None,
    ) -> str:
        """Render refs and validate one exact or portable read-only model query."""
        target = self._resolved_target(target_dialect)
        if target is SqlDialect.PORTABLE:
            raise TransformProjectError("portable is an authored SQL contract, not a target")
        authored = model.metadata.dialect
        if authored is not SqlDialect.PORTABLE and authored is not target:
            raise TransformProjectError(
                f"Model {model.name} is {authored.value} SQL and cannot target {target.value}"
            )

        reference_dialect = target if authored is SqlDialect.PORTABLE else authored
        environment = Environment(undefined=StrictUndefined, autoescape=False)
        environment.globals.clear()
        try:
            compiled = (
                environment.from_string(model.sql)
                .render(
                    ref=lambda reference: self._relation_for_ref(
                        reference,
                        dialect=reference_dialect,
                    )
                )
                .strip()
            )
        except (TemplateError, TransformProjectError) as error:
            raise TransformProjectError(f"Cannot compile model {model.name}") from error
        if compiled.endswith(";"):
            compiled = compiled[:-1].rstrip()
        if authored is SqlDialect.PORTABLE:
            unique_columns = {test.column for test in model.metadata.tests if test.unique}
            try:
                allowed_relations = {
                    self._relation_coordinates_for_ref(reference, dialect=target)
                    for reference in model.refs
                }
                expression = parse_portable_query(
                    compiled,
                    unique_columns=unique_columns,
                    allowed_relations=allowed_relations,
                )
                return render_portable_query(expression, target=target)
            except PortableSqlError as error:
                raise TransformProjectError(
                    f"Invalid portable SQL in model {model.name}: {error}"
                ) from error
        try:
            exact_expression = sqlglot.parse_one(compiled, read=authored.value)
        except sqlglot.errors.ParseError as error:
            raise TransformProjectError(
                f"Invalid {authored.value} SQL in model {model.name}"
            ) from error
        if not isinstance(exact_expression, exp.Query):
            raise TransformProjectError(f"Model {model.name} must contain one read-only query")
        return compiled

    def _validate_references(self) -> None:
        for model in self.models.values():
            for reference in model.refs:
                self.relation_for_ref(reference)

    def _relation_for_ref(self, reference: str, *, dialect: SqlDialect) -> str:
        return self.relation_for_ref(reference, target_dialect=dialect)

    def _resolved_target(self, target: SqlDialect | str | None) -> SqlDialect:
        if target is None:
            return self.target_dialect
        try:
            return SqlDialect(target)
        except ValueError as error:
            raise TransformProjectError(f"Unknown target SQL dialect: {target}") from error

    def _render_relation(self, relation_ref: RelationRef, *, dialect: SqlDialect) -> str:
        if dialect is SqlDialect.BIGQUERY:
            return f"`{'.'.join(relation_ref.coordinates)}`"
        relation = exp.Table(
            this=exp.to_identifier(relation_ref.name, quoted=True),
            db=exp.to_identifier(relation_ref.namespace, quoted=True),
            catalog=(
                None
                if dialect is SqlDialect.POSTGRES
                else exp.to_identifier(relation_ref.catalog, quoted=True)
            ),
        )
        return relation.sql(dialect=dialect.value)

    def _relation_coordinates_for_ref(
        self,
        reference: str,
        *,
        dialect: SqlDialect,
    ) -> tuple[str, ...]:
        relation = self.relation_ref_for_ref(reference)
        if dialect is SqlDialect.POSTGRES:
            return (relation.namespace, relation.name)
        return relation.coordinates
