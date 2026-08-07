"""Conservative SQL-dialect contract for portable transform models."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.tokenizer_core import Token, TokenType
from sqlglot.tokens import Tokenizer

from dander.transform.model import SqlDialect

if TYPE_CHECKING:
    from collections.abc import Collection


class PortableSqlError(ValueError):
    """SQL falls outside Dander's provider-neutral subset."""


_PORTABLE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_TARGET_DIALECTS = frozenset(SqlDialect) - {SqlDialect.PORTABLE}

# Exact node types are intentional: a new sqlglot construct is rejected until Dander assigns it
# portable semantics and adds positive and negative conformance fixtures.
_PORTABLE_NODES: frozenset[type[exp.Expression]] = frozenset(
    {
        exp.Abs,
        exp.Add,
        exp.Alias,
        exp.And,
        exp.Avg,
        exp.Between,
        exp.Boolean,
        exp.Case,
        exp.Cast,
        exp.Ceil,
        exp.Coalesce,
        exp.Column,
        exp.Concat,
        exp.Count,
        exp.CTE,
        exp.DataType,
        exp.DataTypeParam,
        exp.DenseRank,
        exp.Distinct,
        exp.Div,
        exp.EQ,
        exp.Floor,
        exp.From,
        exp.Greatest,
        exp.Group,
        exp.GT,
        exp.GTE,
        exp.Having,
        exp.Identifier,
        exp.If,
        exp.In,
        exp.Is,
        exp.Join,
        exp.Lag,
        exp.LastValue,
        exp.Lead,
        exp.Least,
        exp.Length,
        exp.Like,
        exp.Limit,
        exp.Literal,
        exp.Lower,
        exp.LT,
        exp.LTE,
        exp.Max,
        exp.Min,
        exp.Mod,
        exp.Mul,
        exp.Neg,
        exp.NEQ,
        exp.Not,
        exp.Null,
        exp.Nullif,
        exp.Offset,
        exp.Or,
        exp.Order,
        exp.Ordered,
        exp.Paren,
        exp.Rank,
        exp.Round,
        exp.RowNumber,
        exp.Select,
        exp.Star,
        exp.Sub,
        exp.Substring,
        exp.Subquery,
        exp.Sum,
        exp.Table,
        exp.TableAlias,
        exp.Trim,
        exp.Tuple,
        exp.Union,
        exp.Upper,
        exp.When,
        exp.Where,
        exp.Window,
        exp.WindowSpec,
        exp.With,
    }
)

_PORTABLE_CASTS = frozenset(
    {
        exp.DataType.Type.BIGINT,
        exp.DataType.Type.BOOLEAN,
        exp.DataType.Type.DATE,
        exp.DataType.Type.DECIMAL,
        exp.DataType.Type.DOUBLE,
        exp.DataType.Type.TEXT,
        exp.DataType.Type.TIME,
        exp.DataType.Type.TIMESTAMP,
        exp.DataType.Type.TIMESTAMPTZ,
    }
)

_ORDER_TERMINATORS = frozenset(
    {
        TokenType.FETCH,
        TokenType.HAVING,
        TokenType.LIMIT,
        TokenType.OFFSET,
        TokenType.QUALIFY,
        TokenType.UNION,
        TokenType.WHERE,
    }
)


def parse_portable_query(
    sql: str,
    *,
    unique_columns: Collection[str] = (),
    allowed_relations: Collection[tuple[str, ...]] | None = None,
) -> exp.Query:
    """Parse and validate one query against Dander's closed portable SQL subset."""
    try:
        expression = sqlglot.parse_one(sql, read="bigquery")
    except sqlglot.errors.ParseError as error:
        raise PortableSqlError("invalid portable SQL") from error
    if not isinstance(expression, exp.Query):
        raise PortableSqlError("portable model must contain one read-only query")

    unsupported = sorted(
        {type(node).__name__ for node in expression.walk() if type(node) not in _PORTABLE_NODES}
    )
    if unsupported:
        raise PortableSqlError("unsupported portable SQL nodes: " + ", ".join(unsupported))

    _validate_identifiers(expression)
    _validate_literals(expression)
    _validate_casts(expression)
    _validate_sets_and_joins(expression)
    _validate_stars_and_distinct(expression)
    _require_explicit_null_ordering(sql)
    _validate_deterministic_windows(expression, unique_columns=frozenset(unique_columns))
    _validate_tables(
        expression,
        allowed_relations=None if allowed_relations is None else frozenset(allowed_relations),
    )
    return expression


def render_portable_query(expression: exp.Query, *, target: SqlDialect | str) -> str:
    """Render a previously validated portable AST for one supported warehouse dialect."""
    try:
        dialect = SqlDialect(target)
    except ValueError as error:
        raise PortableSqlError(f"unsupported SQL target dialect: {target}") from error
    if dialect not in _TARGET_DIALECTS:
        raise PortableSqlError("portable is an authored contract, not a render target")
    return expression.sql(dialect=dialect.value)


def _validate_identifiers(expression: exp.Query) -> None:
    for identifier in expression.find_all(exp.Identifier):
        value = identifier.this
        if identifier.args.get("quoted"):
            if isinstance(identifier.parent, exp.Table):
                continue
            raise PortableSqlError("portable logical identifiers must not be quoted")
        if not _PORTABLE_IDENTIFIER.fullmatch(value):
            raise PortableSqlError(
                "portable logical identifiers must use lowercase snake_case names"
            )


def _validate_literals(expression: exp.Query) -> None:
    for literal in expression.find_all(exp.Literal):
        if literal.is_string and unicodedata.normalize("NFC", literal.this) != literal.this:
            raise PortableSqlError("portable string literals must use Unicode NFC normalization")


def _validate_casts(expression: exp.Query) -> None:
    for cast in expression.find_all(exp.Cast):
        if cast.args.get("safe"):
            raise PortableSqlError("portable SQL does not permit safe or try casts")
        target = cast.args.get("to")
        if not isinstance(target, exp.DataType) or target.this not in _PORTABLE_CASTS:
            raise PortableSqlError("portable SQL cast target is unsupported")
        parameters = tuple(target.expressions)
        if target.this is exp.DataType.Type.DECIMAL:
            if _integer_parameters(parameters) != (38, 9):
                raise PortableSqlError("portable DECIMAL casts must declare DECIMAL(38, 9)")
        elif target.this in {
            exp.DataType.Type.TIME,
            exp.DataType.Type.TIMESTAMP,
            exp.DataType.Type.TIMESTAMPTZ,
        }:
            if _integer_parameters(parameters) != (6,):
                raise PortableSqlError("portable time and timestamp casts must declare precision 6")
        elif parameters:
            raise PortableSqlError("portable scalar casts do not accept type parameters")


def _integer_parameters(parameters: tuple[exp.Expression, ...]) -> tuple[int, ...]:
    values: list[int] = []
    for parameter in parameters:
        value = parameter.this if isinstance(parameter, exp.DataTypeParam) else None
        if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
            return ()
        values.append(int(value.this))
    return tuple(values)


def _validate_sets_and_joins(expression: exp.Query) -> None:
    for union in expression.find_all(exp.Union):
        if union.args.get("distinct") is not False or union.args.get("by_name"):
            raise PortableSqlError("portable SQL supports UNION ALL only")
    for join in expression.find_all(exp.Join):
        kind = (join.args.get("kind") or "").upper()
        side = (join.args.get("side") or "").upper()
        method = (join.args.get("method") or "").upper()
        if method == "NATURAL":
            raise PortableSqlError("portable SQL does not permit NATURAL JOIN")
        if kind == "CROSS":
            if join.args.get("on") is not None or join.args.get("using"):
                raise PortableSqlError("portable CROSS JOIN cannot declare ON or USING")
            continue
        if kind not in {"", "INNER", "OUTER"}:
            raise PortableSqlError("portable SQL join type is unsupported")
        if side not in {"", "INNER", "LEFT", "RIGHT", "FULL"}:
            raise PortableSqlError("portable SQL join type is unsupported")
        if kind == "OUTER" and side not in {"LEFT", "RIGHT", "FULL"}:
            raise PortableSqlError("portable OUTER JOIN must declare LEFT, RIGHT, or FULL")
        if join.args.get("on") is None and not join.args.get("using"):
            raise PortableSqlError("portable JOIN must declare ON or USING")


def _validate_stars_and_distinct(expression: exp.Query) -> None:
    for star in expression.find_all(exp.Star):
        if any(star.args.get(key) for key in ("except_", "replace", "rename")):
            raise PortableSqlError("portable wildcard modifiers are unsupported")
    for distinct in expression.find_all(exp.Distinct):
        if distinct.expressions or distinct.args.get("on"):
            raise PortableSqlError("portable SQL does not support DISTINCT ON")


def _validate_deterministic_windows(
    expression: exp.Query,
    *,
    unique_columns: frozenset[str],
) -> None:
    for window in expression.find_all(exp.Window):
        order = window.args.get("order")
        if not isinstance(order, exp.Order) or not order.expressions:
            raise PortableSqlError("portable windows require deterministic ORDER BY")
        final = order.expressions[-1]
        tie_breaker = final.this if isinstance(final, exp.Ordered) else None
        if not isinstance(tie_breaker, exp.Column) or tie_breaker.name not in unique_columns:
            raise PortableSqlError(
                "portable window ORDER BY must end with a column declared unique in model tests"
            )


def _validate_tables(
    expression: exp.Query,
    *,
    allowed_relations: frozenset[tuple[str, ...]] | None,
) -> None:
    cte_names = {cte.alias_or_name for cte in expression.find_all(exp.CTE)}
    for table in expression.find_all(exp.Table):
        coordinates = tuple(part for part in (table.catalog, table.db, table.name) if part)
        if len(coordinates) == 1 and table.name in cte_names:
            continue
        if len(coordinates) < 2:
            raise PortableSqlError("portable models must use Dander ref() relations")
        if allowed_relations is not None and coordinates not in allowed_relations:
            raise PortableSqlError("portable model contains a relation outside its declared refs")


def _require_explicit_null_ordering(sql: str) -> None:
    tokens = Tokenizer(dialect="bigquery").tokenize(sql)
    for index, token in enumerate(tokens):
        if token.token_type is not TokenType.ORDER_BY:
            continue
        depth = _depth_before(tokens, index)
        item: list[Token] = []
        cursor = index + 1
        while cursor < len(tokens):
            current = tokens[cursor]
            current_depth = _depth_before(tokens, cursor)
            if current_depth < depth:
                _require_null_clause(item)
                break
            if current_depth == depth and current.token_type in _ORDER_TERMINATORS:
                _require_null_clause(item)
                break
            if current_depth == depth and current.token_type is TokenType.COMMA:
                _require_null_clause(item)
                item = []
            else:
                item.append(current)
            cursor += 1
        else:
            _require_null_clause(item)


def _depth_before(tokens: list[Token], stop: int) -> int:
    depth = 0
    for token in tokens[:stop]:
        token_type = token.token_type
        if token_type is TokenType.L_PAREN:
            depth += 1
        elif token_type is TokenType.R_PAREN:
            depth -= 1
    return depth


def _require_null_clause(item: list[Token]) -> None:
    if not item:
        raise PortableSqlError("portable ORDER BY must contain an expression")
    words = [token.text.upper() for token in item]
    for index, word in enumerate(words[:-1]):
        if word == "NULLS" and words[index + 1] in {"FIRST", "LAST"}:
            return
    raise PortableSqlError("portable ORDER BY expressions must declare NULLS FIRST or NULLS LAST")
