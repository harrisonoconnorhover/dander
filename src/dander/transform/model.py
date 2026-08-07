"""Transform engine primitives — our owned dbt-replacement.

Per the Decision Log we build this ourselves. A model is SQL + a YAML sidecar. ``ref('other')``
calls are parsed (via sqlglot / Jinja) to build a dependency DAG, topologically sorted, then
executed with materializations that reuse the writer's write patterns. Generic tests (not-null,
unique, accepted-values, relationships) compile to assertion queries. The model YAML is the
metadata spine — it also feeds the catalog and semantic registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Matches the one documented ref() surface form: {{ ref('name') }} / {{ ref("name") }}, with
# arbitrary whitespace at every flex point. Anchoring `ref` directly after `\{\{\s*` (no other
# chars allowed) is what rejects `pref('x')` and `{{ myref('x') }}`; requiring the literal `{{`
# is what rejects bare comment text like `-- references ref('x')`. The `(['"])...\1` backreference
# requires the closing quote to match the opening one.
#
# Evolution path (out of scope for this ticket): if the template surface grows beyond this single
# call form (macros, a two-arg ref('pkg', 'name'), other tags), replace this regex with a Jinja2
# AST visitor (`Environment().parse(sql)` walking `nodes.Call` where the callee is `ref`), which
# reuses the already-pinned `jinja2` dependency per the Decision Log.
_REF_PATTERN: re.Pattern[str] = re.compile(r"\{\{\s*ref\s*\(\s*(['\"])(.*?)\1\s*\)\s*\}\}")


class Materialization(StrEnum):
    VIEW = "view"
    TABLE = "table"
    INCREMENTAL = "incremental"


class SqlDialect(StrEnum):
    """Authored SQL contract for one transform model.

    ``portable`` is Dander's deliberately small, validated SQL subset. The other values declare
    provider-exact SQL and are never translated to a different warehouse dialect.
    """

    PORTABLE = "portable"
    BIGQUERY = "bigquery"
    SNOWFLAKE = "snowflake"
    REDSHIFT = "redshift"
    POSTGRES = "postgres"


@dataclass
class Model:
    """A single transform model: SQL file + its declared metadata."""

    name: str
    sql_path: Path
    materialization: Materialization = Materialization.VIEW
    refs: list[str] = field(default_factory=list)


def parse_refs(sql: str) -> list[str]:
    """Extract ``ref('name')`` dependencies from model SQL to build the DAG.

    Recognizes exactly the ``{{ ref('name') }}`` Jinja call form (single or double quotes,
    arbitrary whitespace inside the braces and around the parens/quotes). Anything else —
    other Jinja expressions, SQL comments mentioning "ref", function-like text such as
    ``pref('x')`` — is ignored. Model names are returned verbatim as written between the
    quotes; validating or resolving them is the downstream DAG builder's responsibility.

    Args:
        sql: Raw model SQL, possibly containing ``{{ ref(...) }}`` calls.

    Returns:
        Referenced model names in order of first appearance, de-duplicated so each name
        appears once (first occurrence wins). Empty list if `sql` contains no refs, including
        for empty-string input.
    """
    names = (match.group(2) for match in _REF_PATTERN.finditer(sql))
    return list(dict.fromkeys(names))
