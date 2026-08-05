"""Compile the executable subset of a visual pipeline graph to BigQuery SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import sqlglot
from sqlglot import exp

from dander.pipeline.graph import TransformationKind
from dander.pipeline.graph_ops import validate_field_wiring
from dander.pipeline.node_config import (
    ExecutableJoinType,
    TargetNodeConfig,
    TransformNodeConfig,
)
from dander.pipeline.operations import (
    ComparisonOperator,
    DefaultValueParams,
    FieldCondition,
    FilterRowsParams,
    MatchLogic,
    OperationSpec,
    TrimWhitespaceParams,
    TruncateStringParams,
)
from dander.writer import (
    BigQueryIncrementalWriter,
    BigQueryReplaceWriter,
    BigQueryScd1Writer,
    BigQueryScd2Writer,
    BigQuerySnapshotWriter,
    BigQueryStorageIncrementalWriter,
    BigQueryStorageScd1Writer,
    WriteField,
    WriteMode,
    WritePattern,
    WriteTarget,
    WriteTransport,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from dander.pipeline.graph import (
        Edge,
        FieldMapping,
        Node,
        NodeField,
        PipelineGraph,
        Transformation,
    )
    from dander.writer.bigquery import _BigQueryClient

_RELATION = re.compile(
    r"^[A-Za-z][A-Za-z0-9-]{4,61}[A-Za-z0-9]\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TYPE = re.compile(
    r"^(?:BOOL|BOOLEAN|BYTES|DATE|DATETIME|FLOAT64|GEOGRAPHY|INT64|INTEGER|JSON|"
    r"NUMERIC|BIGNUMERIC|STRING|TIME|TIMESTAMP)$",
    re.IGNORECASE,
)
_ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "CAST",
        "COALESCE",
        "CONCAT",
        "CURRENT_DATE",
        "CURRENT_DATETIME",
        "CURRENT_TIMESTAMP",
        "DATE",
        "DATETIME",
        "IF",
        "IFNULL",
        "LENGTH",
        "LOWER",
        "NULLIF",
        "REGEXP_EXTRACT",
        "REGEXP_REPLACE",
        "REPLACE",
        "ROUND",
        "SAFE_CAST",
        "SUBSTRING",
        "TIMESTAMP",
        "TRIM",
        "UPPER",
    }
)


class PipelineCompileError(ValueError):
    """Raised when a valid declarative graph has no safe executable interpretation."""


@dataclass(frozen=True)
class CompiledTarget:
    """A target SELECT plus its resolved write contract."""

    node_id: str
    query: str
    write_mode: WriteMode
    target: WriteTarget


@dataclass(frozen=True)
class PreparedTargetWriter:
    """A concrete writer bound to a graph target without performing a write."""

    writer: WritePattern
    target: WriteTarget

    def write(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Send records through the selected idempotent write pattern."""
        return self.writer.write(records, self.target)


def compile_target(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    source_relations: Mapping[str, str],
    default_project: str | None = None,
) -> CompiledTarget:
    """Compile one source dependency subgraph into a BigQuery target SELECT.

    Linear mappings and explicit two-input transform joins are executable. Legacy edge joins
    remain declarative because that shape makes the right input and output the same node.
    """
    validate_field_wiring(graph)
    nodes = {node.id: node for node in graph.nodes}
    try:
        target = nodes[target_node_id]
    except KeyError as error:
        raise PipelineCompileError(f"Unknown target node {target_node_id!r}") from error
    config = target.config
    if target.type != "target" or not isinstance(config, TargetNodeConfig) or config.writer is None:
        raise PipelineCompileError(
            f"Node {target_node_id!r} must be a target with writer configuration"
        )

    incoming: dict[str, list[Edge]] = {node_id: [] for node_id in nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge)
    compiler = _GraphSqlCompiler(
        nodes=nodes,
        incoming=incoming,
        source_relations=source_relations,
    )
    final_alias = compiler.compile_node(target_node_id)

    destination = config.writer.destination
    project = destination.project or default_project
    if project is None:
        raise PipelineCompileError(
            f"Target node {target_node_id!r} must set destination.project for compilation"
        )
    query = (
        "WITH\n"
        + ",\n".join(compiler.ctes)
        + f"\nSELECT {', '.join(_quote(field.name) for field in target.fields)}\n"
        + f"FROM {_quote(final_alias)}"
    )
    return CompiledTarget(
        node_id=target_node_id,
        query=query,
        write_mode=config.writer.write_mode,
        target=WriteTarget(
            project=project,
            dataset=destination.dataset,
            table=destination.table,
            business_key=tuple(destination.business_key),
            schema=tuple(
                WriteField(name=field.name, data_type=field.cast_to or field.type)
                for field in target.fields
            ),
        ),
    )


class _GraphSqlCompiler:
    """Stateful recursive CTE builder for one target's dependency subgraph."""

    def __init__(
        self,
        *,
        nodes: Mapping[str, Node],
        incoming: Mapping[str, list[Edge]],
        source_relations: Mapping[str, str],
    ) -> None:
        self._nodes = nodes
        self._incoming = incoming
        self._source_relations = source_relations
        self._aliases: dict[str, str] = {}
        self.ctes: list[str] = []

    def compile_node(self, node_id: str) -> str:
        """Compile dependencies first and return this node's CTE alias."""
        if node_id in self._aliases:
            return self._aliases[node_id]
        node = self._nodes[node_id]
        edges = self._incoming[node_id]
        if node.type == "source":
            return self._compile_source(node, edges)
        if node.type not in {"transform", "target"}:
            raise PipelineCompileError(
                f"Node {node.id!r} has unsupported executable type {node.type!r}"
            )
        if any(edge.join is not None for edge in edges):
            raise PipelineCompileError(
                "Legacy edge joins are declarative only; use transform.config.join with two "
                "incoming edges and a distinct transform output"
            )
        if len(edges) == 1:
            if isinstance(node.config, TransformNodeConfig) and node.config.join is not None:
                raise PipelineCompileError(
                    f"Join transform {node.id!r} requires exactly two incoming edges"
                )
            upstream = self.compile_node(edges[0].source)
            return self._append_projection(
                node,
                [(edges[0], "source")],
                from_sql=f"{_quote(upstream)} AS source",
            )
        if (
            len(edges) == 2
            and isinstance(node.config, TransformNodeConfig)
            and node.config.join is not None
        ):
            return self._compile_join(node, edges)
        raise PipelineCompileError(
            f"Node {node.id!r} requires one incoming edge, or two with transform.config.join"
        )

    def _compile_source(self, node: Node, edges: list[Edge]) -> str:
        if edges:
            raise PipelineCompileError(f"Source node {node.id!r} cannot have an incoming edge")
        relation = self._source_relations.get(node.id)
        if relation is None:
            raise PipelineCompileError(f"Missing source relation for node {node.id!r}")
        if not _RELATION.fullmatch(relation):
            raise PipelineCompileError(
                f"Source relation for node {node.id!r} must be project.dataset.table"
            )
        columns = ", ".join(_compile_source_field(field) for field in node.fields)
        if not columns:
            raise PipelineCompileError(f"Source node {node.id!r} must declare fields")
        alias = self._next_alias()
        self.ctes.append(f"{_quote(alias)} AS (\n  SELECT {columns} FROM `{relation}`\n)")
        self._aliases[node.id] = alias
        return alias

    def _compile_join(self, node: Node, edges: list[Edge]) -> str:
        assert isinstance(node.config, TransformNodeConfig)
        join = node.config.join
        assert join is not None
        by_source = {edge.source: edge for edge in edges}
        expected = {join.left_input, join.right_input}
        if set(by_source) != expected:
            raise PipelineCompileError(
                f"Join transform {node.id!r} inputs must match its incoming edges"
            )
        left_node = self._nodes[join.left_input]
        right_node = self._nodes[join.right_input]
        left_fields = {field.name for field in left_node.fields}
        right_fields = {field.name for field in right_node.fields}
        for key in join.keys:
            if key.left not in left_fields or key.right not in right_fields:
                raise PipelineCompileError(
                    f"Join transform {node.id!r} references an undeclared key field"
                )
        left_alias = self.compile_node(join.left_input)
        right_alias = self.compile_node(join.right_input)
        join_keyword = {
            ExecutableJoinType.INNER: "INNER JOIN",
            ExecutableJoinType.LEFT: "LEFT JOIN",
            ExecutableJoinType.RIGHT: "RIGHT JOIN",
            ExecutableJoinType.FULL: "FULL OUTER JOIN",
        }[join.type]
        condition = " AND ".join(
            f"lhs.{_quote(key.left)} = rhs.{_quote(key.right)}" for key in join.keys
        )
        from_sql = (
            f"{_quote(left_alias)} AS lhs\n"
            f"  {join_keyword} {_quote(right_alias)} AS rhs ON {condition}"
        )
        return self._append_projection(
            node,
            [
                (by_source[join.left_input], "lhs"),
                (by_source[join.right_input], "rhs"),
            ],
            from_sql=from_sql,
        )

    def _append_projection(
        self,
        node: Node,
        edge_aliases: list[tuple[Edge, str]],
        *,
        from_sql: str,
    ) -> str:
        mappings: dict[str, tuple[FieldMapping, str]] = {}
        mapping_count = 0
        for edge, source_alias in edge_aliases:
            if not edge.mappings:
                raise PipelineCompileError(f"Edge {edge.source!r}->{edge.target!r} has no mappings")
            for mapping in edge.mappings:
                mapping_count += 1
                if mapping.target in mappings:
                    raise PipelineCompileError(
                        f"Node {node.id!r} maps target field {mapping.target!r} more than once"
                    )
                mappings[mapping.target] = (mapping, source_alias)
        if len(mappings) != mapping_count:
            raise AssertionError("Duplicate mapping validation did not fail")
        missing = [field.name for field in node.fields if field.name not in mappings]
        if missing:
            raise PipelineCompileError(f"Node {node.id!r} does not map target field {missing[0]!r}")
        projected = [
            _compile_mapping(
                mappings[field.name][0],
                field.cast_to,
                alias=mappings[field.name][1],
            )
            for field in node.fields
        ]
        select_list = ",\n    ".join(projected)
        alias = self._next_alias()
        self.ctes.append(f"{_quote(alias)} AS (\n  SELECT\n    {select_list}\n  FROM {from_sql}\n)")
        self._aliases[node.id] = alias
        if isinstance(node.config, TransformNodeConfig) and node.config.operations:
            return self._append_operations(node, alias, node.config.operations)
        return alias

    def _append_operations(
        self,
        node: Node,
        upstream_alias: str,
        operations: list[OperationSpec],
    ) -> str:
        """Compile ordered transform operations as explicit schema-preserving CTEs."""
        fields = {field.name: field for field in node.fields}
        alias = upstream_alias
        for operation in operations:
            params = operation.params
            if isinstance(params, FilterRowsParams):
                select_list = ",\n    ".join(
                    f"source.{_quote(field.name)} AS {_quote(field.name)}" for field in node.fields
                )
                predicate = _compile_filter(params)
                cte_sql = (
                    "SELECT\n"
                    f"    {select_list}\n"
                    f"  FROM {_quote(alias)} AS source\n"
                    f"  WHERE {predicate}"
                )
            else:
                if not isinstance(
                    params,
                    (TruncateStringParams, TrimWhitespaceParams, DefaultValueParams),
                ):
                    raise AssertionError(f"Unexpected operation params: {type(params).__name__}")
                field_name = params.field
                field = fields[field_name]
                source = f"source.{_quote(field_name)}"
                if isinstance(params, TrimWhitespaceParams):
                    _require_string_operation(node.id, field_name, field.cast_to or field.type)
                    replacement = f"TRIM({source})"
                elif isinstance(params, TruncateStringParams):
                    _require_string_operation(node.id, field_name, field.cast_to or field.type)
                    replacement = f"SUBSTR({source}, 1, {params.max_length})"
                elif isinstance(params, DefaultValueParams):
                    data_type = _operation_type(node.id, field_name, field.cast_to or field.type)
                    replacement = (
                        f"COALESCE({source}, CAST({_literal(params.default)} AS {data_type}))"
                    )
                select_list = ",\n    ".join(
                    (
                        f"{replacement} AS {_quote(declared.name)}"
                        if declared.name == field_name
                        else f"source.{_quote(declared.name)} AS {_quote(declared.name)}"
                    )
                    for declared in node.fields
                )
                cte_sql = f"SELECT\n    {select_list}\n  FROM {_quote(alias)} AS source"
            alias = self._next_alias()
            self.ctes.append(f"{_quote(alias)} AS (\n  {cte_sql}\n)")
            self._aliases[node.id] = alias
        return alias

    def _next_alias(self) -> str:
        return f"_node_{len(self.ctes)}"


def prepare_target_writer(
    target_node: Node,
    *,
    default_project: str,
    client: object | None = None,
) -> PreparedTargetWriter:
    """Resolve target-node configuration to one concrete BigQuery writer."""
    config = target_node.config
    if target_node.type != "target" or not isinstance(config, TargetNodeConfig):
        raise PipelineCompileError(f"Node {target_node.id!r} is not a configured target")
    if config.writer is None:
        raise PipelineCompileError(f"Target node {target_node.id!r} has no writer configuration")
    writer_config = config.writer
    destination = writer_config.destination
    project = destination.project or default_project
    typed_client = cast("_BigQueryClient | None", client)
    match writer_config.write_mode:
        case WriteMode.SCD1:
            if writer_config.transport is WriteTransport.STORAGE_WRITE:
                writer: WritePattern = BigQueryStorageScd1Writer(
                    project=project,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
            else:
                writer = BigQueryScd1Writer(
                    project=project,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
        case WriteMode.SCD2:
            writer = BigQueryScd2Writer(
                project=project,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
                schema_evolution=writer_config.schema_evolution,
            )
        case WriteMode.INCREMENTAL:
            assert writer_config.cursor_field is not None
            if writer_config.transport is WriteTransport.STORAGE_WRITE:
                writer = BigQueryStorageIncrementalWriter(
                    project=project,
                    cursor_field=writer_config.cursor_field,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
            else:
                writer = BigQueryIncrementalWriter(
                    project=project,
                    cursor_field=writer_config.cursor_field,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
        case WriteMode.SNAPSHOT:
            partitioning = writer_config.partitioning
            if partitioning is None or partitioning.field is None:
                raise PipelineCompileError(
                    "Snapshot target execution requires field-based partitioning"
                )
            writer = BigQuerySnapshotWriter(
                project=project,
                snapshot_field=partitioning.field,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
                schema_evolution=writer_config.schema_evolution,
            )
        case WriteMode.REPLACE:
            writer = BigQueryReplaceWriter(
                project=project,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
            )
    return PreparedTargetWriter(
        writer=writer,
        target=WriteTarget(
            project=project,
            dataset=destination.dataset,
            table=destination.table,
            business_key=tuple(destination.business_key),
            schema=tuple(
                WriteField(name=field.name, data_type=field.cast_to or field.type)
                for field in target_node.fields
            ),
        ),
    )


def _compile_mapping(mapping: FieldMapping, cast_to: str | None, *, alias: str) -> str:
    transformation = mapping.transformation
    if transformation is None or transformation.kind is TransformationKind.DIRECT:
        if mapping.source is None:
            raise PipelineCompileError("A direct mapping must name its source field")
        expression = f"{alias}.{_quote(mapping.source)}"
    elif transformation.kind is TransformationKind.CONSTANT:
        expression = exp.convert(transformation.constant).sql(dialect="bigquery")
    elif transformation.kind is TransformationKind.EXPRESSION:
        expression = _compile_expression(transformation, alias=alias)
    else:
        expression = _compile_custom(transformation, alias=alias)
    if cast_to is not None:
        if not _TYPE.fullmatch(cast_to):
            raise PipelineCompileError(f"Unsupported target cast type {cast_to!r}")
        expression = f"SAFE_CAST({expression} AS {cast_to.upper()})"
    return f"{expression} AS {_quote(mapping.target)}"


def _compile_source_field(field: NodeField) -> str:
    column = _quote(field.name)
    if field.cast_to is None:
        return column
    if not _TYPE.fullmatch(field.cast_to):
        raise PipelineCompileError(f"Unsupported source cast type {field.cast_to!r}")
    return f"SAFE_CAST({column} AS {field.cast_to.upper()}) AS {column}"


def _compile_expression(transformation: Transformation, *, alias: str) -> str:
    assert transformation.expression is not None
    try:
        parsed = sqlglot.parse_one(transformation.expression, read="bigquery")
    except sqlglot.errors.ParseError as error:
        raise PipelineCompileError("Transformation expression is not valid BigQuery SQL") from error
    if isinstance(parsed, (exp.Query, exp.Subquery)) or any(
        isinstance(node, (exp.Query, exp.Subquery, exp.Table, exp.Star, exp.Parameter))
        for node in parsed.walk()
    ):
        raise PipelineCompileError("Transformation expressions must be scalar and row-local")
    declared = set(transformation.inputs)
    referenced = {column.name for column in parsed.find_all(exp.Column)}
    if referenced != declared:
        raise PipelineCompileError(
            "Transformation expression columns must exactly match its declared inputs"
        )
    for function in parsed.find_all(exp.Func):
        name = function.sql_name().upper()
        if name == "TRY_CAST":
            name = "SAFE_CAST"
        if name not in _ALLOWED_FUNCTIONS:
            raise PipelineCompileError(f"SQL function {name!r} is not allow-listed")
    qualified = parsed.transform(
        lambda node: exp.column(node.name, table=alias) if isinstance(node, exp.Column) else node
    )
    return qualified.sql(dialect="bigquery")


def _compile_custom(transformation: Transformation, *, alias: str) -> str:
    inputs = [f"{alias}.{_quote(name)}" for name in transformation.inputs]
    arguments = [
        exp.convert(value).sql(dialect="bigquery") for value in transformation.arguments.values()
    ]
    values = [*inputs, *arguments]
    match transformation.function:
        case "transforms.lower":
            _require_arity(transformation.function, values, 1)
            return f"LOWER({values[0]})"
        case "transforms.upper":
            _require_arity(transformation.function, values, 1)
            return f"UPPER({values[0]})"
        case "transforms.trim":
            _require_arity(transformation.function, values, 1)
            return f"TRIM({values[0]})"
        case "transforms.normalize_phone":
            if len(inputs) != 1:
                raise PipelineCompileError(
                    "Custom transform 'transforms.normalize_phone' requires one input"
                )
            if transformation.arguments:
                raise PipelineCompileError(
                    "Custom transform 'transforms.normalize_phone' does not accept arguments"
                )
            return f"REGEXP_REPLACE(CAST({inputs[0]} AS STRING), r'[^0-9+]', '')"
        case _:
            raise PipelineCompileError(
                f"Custom transform {transformation.function!r} is not allow-listed"
            )


def _compile_filter(params: FilterRowsParams) -> str:
    joiner = " AND " if params.logic is MatchLogic.ALL else " OR "
    return joiner.join(f"({_compile_condition(condition)})" for condition in params.conditions)


def _compile_condition(condition: FieldCondition) -> str:
    field = f"source.{_quote(condition.field)}"
    if condition.op is ComparisonOperator.IS_NULL:
        return f"{field} IS NULL"
    if condition.op is ComparisonOperator.IS_NOT_NULL:
        return f"{field} IS NOT NULL"
    if condition.op in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
        assert isinstance(condition.value, list)
        keyword = "IN" if condition.op is ComparisonOperator.IN else "NOT IN"
        values = ", ".join(_literal(value) for value in condition.value)
        return f"{field} {keyword} ({values})"
    assert condition.value is not None and not isinstance(condition.value, list)
    operator = {
        ComparisonOperator.EQ: "=",
        ComparisonOperator.NE: "!=",
        ComparisonOperator.GT: ">",
        ComparisonOperator.GTE: ">=",
        ComparisonOperator.LT: "<",
        ComparisonOperator.LTE: "<=",
    }[condition.op]
    return f"{field} {operator} {_literal(condition.value)}"


def _literal(value: object) -> str:
    return exp.convert(value).sql(dialect="bigquery")


def _require_string_operation(node_id: str, field_name: str, data_type: str) -> None:
    if _operation_type(node_id, field_name, data_type) != "STRING":
        raise PipelineCompileError(
            f"Transform node {node_id!r} applies a string operation to non-STRING field "
            f"{field_name!r}"
        )


def _operation_type(node_id: str, field_name: str, data_type: str) -> str:
    if not _TYPE.fullmatch(data_type):
        raise PipelineCompileError(
            f"Transform node {node_id!r} operation field {field_name!r} has unsupported type"
        )
    return data_type.upper()


def _require_arity(function: str | None, values: list[str], count: int) -> None:
    if len(values) != count:
        raise PipelineCompileError(f"Custom transform {function!r} requires {count} argument")


def _quote(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise PipelineCompileError(f"Unsafe BigQuery identifier {identifier!r}")
    return f"`{identifier}`"
