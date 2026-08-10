"""Compile the executable visual-graph subset to a provider-neutral relational AST."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import reduce
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
from dander.transform.model import SqlDialect
from dander.warehouse import RelationRef
from dander.writer.base import WriteField, WriteMode, WriteTarget

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
    from dander.writer.base import WritePattern

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
    _query_ast: exp.Query = field(repr=False, compare=False)

    @property
    def query_ast(self) -> exp.Query:
        """Return an isolated copy of the provider-neutral relational plan."""
        copied = self._query_ast.copy()
        assert isinstance(copied, exp.Query)
        return copied

    def render(self, target_dialect: SqlDialect | str) -> str:
        """Render the graph AST for one warehouse without recompiling graph nodes."""
        return render_graph_query(self._query_ast, target_dialect=target_dialect)


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
    source_relations: Mapping[str, RelationRef | str],
    default_catalog: str | None = None,
    default_project: str | None = None,
) -> CompiledTarget:
    """Compile one source dependency subgraph into a provider-neutral target SELECT.

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
    if default_catalog is None:
        default_catalog = default_project
    elif default_project is not None and default_project != default_catalog:
        raise PipelineCompileError("default_catalog and legacy default_project must match")
    query_ast = exp.Select(
        expressions=[_column(field.name) for field in target.fields],
        from_=exp.From(this=_cte_table(final_alias)),
    )
    query_ast.set("with_", exp.With(expressions=compiler.ctes))
    query = render_graph_query(query_ast, target_dialect=SqlDialect.BIGQUERY)
    try:
        target_relation = destination.relation_ref(default_catalog=default_catalog)
    except ValueError as error:
        raise PipelineCompileError(
            f"Target node {target_node_id!r} has an invalid destination"
        ) from error
    return CompiledTarget(
        node_id=target_node_id,
        query=query,
        write_mode=config.writer.write_mode,
        target=WriteTarget(
            relation=target_relation,
            business_key=tuple(destination.business_key),
            schema=tuple(
                WriteField(
                    name=field.name,
                    data_type=field.cast_to or field.type,
                    extensions=field.extensions,
                )
                for field in target.fields
            ),
        ),
        _query_ast=query_ast,
    )


def render_graph_query(
    expression: exp.Query,
    *,
    target_dialect: SqlDialect | str,
) -> str:
    """Render a compiled graph AST, rejecting semantics a target cannot preserve."""
    try:
        target = SqlDialect(target_dialect)
    except ValueError as error:
        raise PipelineCompileError(f"Unknown graph SQL target: {target_dialect}") from error
    if target is SqlDialect.PORTABLE:
        raise PipelineCompileError("portable is a graph contract, not a render target")
    if target in {SqlDialect.SNOWFLAKE, SqlDialect.POSTGRES} and expression.find(exp.TryCast):
        raise PipelineCompileError(
            f"Graph safe-cast semantics are not yet exact for {target.value}"
        )
    rendered = expression.copy()
    if target is SqlDialect.POSTGRES:
        transformed = rendered.transform(_postgres_relation)
        assert isinstance(transformed, exp.Query)
        rendered = transformed
    return rendered.sql(dialect=target.value, pretty=True)


def _postgres_relation(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Table) and node.catalog:
        updated = node.copy()
        updated.set("catalog", None)
        return updated
    return node


class _GraphSqlCompiler:
    """Stateful recursive CTE builder for one target's dependency subgraph."""

    def __init__(
        self,
        *,
        nodes: Mapping[str, Node],
        incoming: Mapping[str, list[Edge]],
        source_relations: Mapping[str, RelationRef | str],
    ) -> None:
        self._nodes = nodes
        self._incoming = incoming
        self._source_relations = source_relations
        self._aliases: dict[str, str] = {}
        self.ctes: list[exp.CTE] = []

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
                from_table=_cte_table(upstream, alias="source"),
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
        if isinstance(relation, str):
            if not _RELATION.fullmatch(relation):
                raise PipelineCompileError(
                    f"Source relation for node {node.id!r} must be catalog.namespace.relation"
                )
            relation_ref = _parse_relation(relation)
        else:
            relation_ref = relation
        columns = [_compile_source_field(field) for field in node.fields]
        if not columns:
            raise PipelineCompileError(f"Source node {node.id!r} must declare fields")
        alias = self._next_alias()
        query = exp.Select(expressions=columns, from_=exp.From(this=_relation_table(relation_ref)))
        self.ctes.append(_cte(alias, query))
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
        side, kind = {
            ExecutableJoinType.INNER: (None, "INNER"),
            ExecutableJoinType.LEFT: ("LEFT", None),
            ExecutableJoinType.RIGHT: ("RIGHT", None),
            ExecutableJoinType.FULL: ("FULL", "OUTER"),
        }[join.type]
        conditions: list[exp.Expression] = [
            exp.EQ(this=_column(key.left, table="lhs"), expression=_column(key.right, table="rhs"))
            for key in join.keys
        ]
        condition = reduce(_and, conditions)
        join_expression = exp.Join(
            this=_cte_table(right_alias, alias="rhs"),
            side=side,
            kind=kind,
            on=condition,
        )
        return self._append_projection(
            node,
            [
                (by_source[join.left_input], "lhs"),
                (by_source[join.right_input], "rhs"),
            ],
            from_table=_cte_table(left_alias, alias="lhs"),
            joins=[join_expression],
        )

    def _append_projection(
        self,
        node: Node,
        edge_aliases: list[tuple[Edge, str]],
        *,
        from_table: exp.Table,
        joins: list[exp.Join] | None = None,
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
        alias = self._next_alias()
        query = exp.Select(
            expressions=projected,
            from_=exp.From(this=from_table),
            joins=joins or [],
        )
        self.ctes.append(_cte(alias, query))
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
                select_list = [
                    _alias(_column(field.name, table="source"), field.name) for field in node.fields
                ]
                predicate = _compile_filter(params)
                query = exp.Select(
                    expressions=select_list,
                    from_=exp.From(this=_cte_table(alias, alias="source")),
                    where=exp.Where(this=predicate),
                )
            else:
                if not isinstance(
                    params,
                    (TruncateStringParams, TrimWhitespaceParams, DefaultValueParams),
                ):
                    raise AssertionError(f"Unexpected operation params: {type(params).__name__}")
                field_name = params.field
                field = fields[field_name]
                source = _column(field_name, table="source")
                if isinstance(params, TrimWhitespaceParams):
                    _require_string_operation(node.id, field_name, field.cast_to or field.type)
                    replacement: exp.Expression = exp.Trim(this=source)
                elif isinstance(params, TruncateStringParams):
                    _require_string_operation(node.id, field_name, field.cast_to or field.type)
                    replacement = exp.Substring(
                        this=source,
                        start=exp.Literal.number(1),
                        length=exp.Literal.number(params.max_length),
                    )
                elif isinstance(params, DefaultValueParams):
                    data_type = _operation_type(node.id, field_name, field.cast_to or field.type)
                    replacement = exp.Coalesce(
                        this=source,
                        expressions=[
                            exp.Cast(this=exp.convert(params.default), to=_data_type(data_type))
                        ],
                    )
                select_list = [
                    _alias(
                        replacement
                        if declared.name == field_name
                        else _column(declared.name, table="source"),
                        declared.name,
                    )
                    for declared in node.fields
                ]
                query = exp.Select(
                    expressions=select_list,
                    from_=exp.From(this=_cte_table(alias, alias="source")),
                )
            alias = self._next_alias()
            self.ctes.append(_cte(alias, query))
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
    """Compatibility wrapper for BigQuery writer preparation at its provider boundary."""
    from dander.providers.bigquery.graph import prepare_bigquery_target_writer

    return prepare_bigquery_target_writer(
        target_node,
        default_catalog=default_project,
        client=client,
    )


def _compile_mapping(
    mapping: FieldMapping,
    cast_to: str | None,
    *,
    alias: str,
) -> exp.Expression:
    transformation = mapping.transformation
    if transformation is None or transformation.kind is TransformationKind.DIRECT:
        if mapping.source is None:
            raise PipelineCompileError("A direct mapping must name its source field")
        expression: exp.Expression = _column(mapping.source, table=alias)
    elif transformation.kind is TransformationKind.CONSTANT:
        expression = cast("exp.Expression", exp.convert(transformation.constant))
    elif transformation.kind is TransformationKind.EXPRESSION:
        expression = _compile_expression(transformation, alias=alias)
    else:
        expression = _compile_custom(transformation, alias=alias)
    if cast_to is not None:
        if not _TYPE.fullmatch(cast_to):
            raise PipelineCompileError(f"Unsupported target cast type {cast_to!r}")
        expression = exp.TryCast(this=expression, to=_data_type(cast_to))
    return _alias(expression, mapping.target)


def _compile_source_field(field: NodeField) -> exp.Expression:
    column = _column(field.name)
    if field.cast_to is None:
        return column
    if not _TYPE.fullmatch(field.cast_to):
        raise PipelineCompileError(f"Unsupported source cast type {field.cast_to!r}")
    return _alias(
        exp.TryCast(this=column, to=_data_type(field.cast_to)),
        field.name,
    )


def _compile_expression(transformation: Transformation, *, alias: str) -> exp.Expression:
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
    return cast("exp.Expression", qualified)


def _compile_custom(transformation: Transformation, *, alias: str) -> exp.Expression:
    inputs = [_column(name, table=alias) for name in transformation.inputs]
    arguments = [
        cast("exp.Expression", exp.convert(value)) for value in transformation.arguments.values()
    ]
    values: list[exp.Expression] = [*inputs, *arguments]
    match transformation.function:
        case "transforms.lower":
            _require_arity(transformation.function, values, 1)
            return exp.Lower(this=values[0])
        case "transforms.upper":
            _require_arity(transformation.function, values, 1)
            return exp.Upper(this=values[0])
        case "transforms.trim":
            _require_arity(transformation.function, values, 1)
            return exp.Trim(this=values[0])
        case "transforms.normalize_phone":
            if len(inputs) != 1:
                raise PipelineCompileError(
                    "Custom transform 'transforms.normalize_phone' requires one input"
                )
            if transformation.arguments:
                raise PipelineCompileError(
                    "Custom transform 'transforms.normalize_phone' does not accept arguments"
                )
            return exp.RegexpReplace(
                this=exp.Cast(this=inputs[0], to=_data_type("STRING")),
                expression=exp.Literal.string("[^0-9+]"),
                replacement=exp.Literal.string(""),
            )
        case _:
            raise PipelineCompileError(
                f"Custom transform {transformation.function!r} is not allow-listed"
            )


def _compile_filter(params: FilterRowsParams) -> exp.Expression:
    combinator = _and if params.logic is MatchLogic.ALL else _or
    return reduce(combinator, (_compile_condition(condition) for condition in params.conditions))


def _compile_condition(condition: FieldCondition) -> exp.Expression:
    field = _column(condition.field, table="source")
    if condition.op is ComparisonOperator.IS_NULL:
        return exp.Is(this=field, expression=exp.Null())
    if condition.op is ComparisonOperator.IS_NOT_NULL:
        return exp.Not(this=exp.Is(this=field, expression=exp.Null()))
    if condition.op in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
        assert isinstance(condition.value, list)
        contained: exp.Expression = exp.In(
            this=field,
            expressions=[exp.convert(value) for value in condition.value],
        )
        return exp.Not(this=contained) if condition.op is ComparisonOperator.NOT_IN else contained
    assert condition.value is not None and not isinstance(condition.value, list)
    operator: type[exp.Binary] = {
        ComparisonOperator.EQ: exp.EQ,
        ComparisonOperator.NE: exp.NEQ,
        ComparisonOperator.GT: exp.GT,
        ComparisonOperator.GTE: exp.GTE,
        ComparisonOperator.LT: exp.LT,
        ComparisonOperator.LTE: exp.LTE,
    }[condition.op]
    return cast("exp.Expression", operator(this=field, expression=exp.convert(condition.value)))


def _and(left: exp.Expression, right: exp.Expression) -> exp.Expression:
    return exp.And(this=left, expression=right)


def _or(left: exp.Expression, right: exp.Expression) -> exp.Expression:
    return exp.Or(this=left, expression=right)


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


def _require_arity(function: str | None, values: list[exp.Expression], count: int) -> None:
    if len(values) != count:
        raise PipelineCompileError(f"Custom transform {function!r} requires {count} argument")


def _identifier(identifier: str, *, quoted: bool = True) -> exp.Identifier:
    if not _IDENTIFIER.fullmatch(identifier):
        raise PipelineCompileError(f"Unsafe graph identifier {identifier!r}")
    return exp.to_identifier(identifier, quoted=quoted)


def _column(name: str, *, table: str | None = None) -> exp.Column:
    return exp.Column(
        this=_identifier(name),
        table=_identifier(table, quoted=False) if table is not None else None,
    )


def _alias(expression: exp.Expression, name: str) -> exp.Alias:
    return exp.Alias(this=expression, alias=_identifier(name))


def _cte_table(name: str, *, alias: str | None = None) -> exp.Table:
    return exp.Table(
        this=_identifier(name),
        alias=(
            exp.TableAlias(this=_identifier(alias, quoted=False)) if alias is not None else None
        ),
    )


def _relation_table(relation: RelationRef) -> exp.Table:
    return exp.Table(
        this=_identifier(relation.name),
        db=_identifier(relation.namespace),
        catalog=exp.to_identifier(relation.catalog, quoted=True),
    )


def _cte(name: str, query: exp.Query) -> exp.CTE:
    return exp.CTE(
        this=query,
        alias=exp.TableAlias(this=_identifier(name)),
    )


def _parse_relation(value: str) -> RelationRef:
    catalog, namespace, name = value.split(".", maxsplit=2)
    return RelationRef(catalog=catalog, namespace=namespace, name=name)


def _data_type(value: str) -> exp.DataType:
    try:
        return exp.DataType.build(value, dialect="bigquery")
    except (TypeError, ValueError) as error:
        raise PipelineCompileError(f"Unsupported graph type {value!r}") from error
