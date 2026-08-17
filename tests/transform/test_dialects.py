"""Portable SQL subset and warehouse-dialect rendering tests."""

from __future__ import annotations

import pytest
from sqlglot import exp

from dander.transform import PortableSqlError, SqlDialect
from dander.transform.dialects import parse_portable_query, render_portable_query


@pytest.mark.parametrize("target", ["bigquery", "snowflake", "redshift", "postgres"])
def test_portable_projection_filter_join_aggregation_and_render(target: str) -> None:
    query = parse_portable_query(
        """
        SELECT
          account.account_id,
          COUNT(*) AS contact_count,
          SUM(CAST(contact.score AS DECIMAL(38, 9))) AS total_score,
          COALESCE(MAX(contact.title), 'unknown') AS latest_title
        FROM `portable.raw.accounts` AS account
        LEFT JOIN `portable.raw.contacts` AS contact
          ON account.account_id = contact.account_id
        WHERE contact.is_deleted = FALSE OR contact.is_deleted IS NULL
        GROUP BY account.account_id
        HAVING COUNT(*) > 0
        """
    )

    rendered = render_portable_query(query, target=target)

    assert "contact_count" in rendered
    assert "LEFT JOIN" in rendered
    assert "GROUP BY" in rendered
    assert SqlDialect(target) is not SqlDialect.PORTABLE


def test_portable_union_all_and_deterministic_window() -> None:
    query = parse_portable_query(
        """
        SELECT
          id,
          ROW_NUMBER() OVER (
            PARTITION BY account_id
            ORDER BY updated_at DESC NULLS LAST, id ASC NULLS LAST
          ) AS row_num
        FROM `portable.raw.contacts`
        UNION ALL
        SELECT id, 1 AS row_num
        FROM `portable.raw.archived_contacts`
        """,
        unique_columns={"id"},
    )

    assert isinstance(query, exp.Union)
    assert "ROW_NUMBER" in render_portable_query(query, target="snowflake")


def test_snowflake_quotes_lowercase_columns_aliases_joins_and_ctes() -> None:
    query = parse_portable_query(
        """
        WITH filtered_contacts AS (
          SELECT contact.id AS contact_id, contact.title AS contact_title
          FROM `portable.raw.contacts` AS contact
          WHERE contact.id IS NOT NULL
        )
        SELECT filtered_contacts.contact_id AS id, account.title AS account_title
        FROM filtered_contacts
        JOIN `portable.raw.accounts` AS account
          ON filtered_contacts.contact_id = account.id
        """
    )

    rendered = render_portable_query(query, target="snowflake")

    assert 'WITH "filtered_contacts" AS' in rendered
    assert 'SELECT "contact"."id" AS "contact_id"' in rendered
    assert 'FROM "portable"."raw"."contacts" AS "contact"' in rendered
    assert 'SELECT "filtered_contacts"."contact_id" AS "id"' in rendered
    assert 'JOIN "portable"."raw"."accounts" AS "account"' in rendered
    assert '"filtered_contacts"."contact_id" = "account"."id"' in rendered


@pytest.mark.parametrize("target", ["bigquery", "redshift", "postgres"])
def test_snowflake_identifier_quoting_does_not_change_other_targets(target: str) -> None:
    query = parse_portable_query(
        "SELECT contact.id AS contact_id FROM `portable.raw.contacts` AS contact"
    )

    render_portable_query(query, target="snowflake")
    rendered = render_portable_query(query, target=target)

    assert "contact.id AS contact_id" in rendered


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT id FROM `portable.raw.t` ORDER BY id", "NULLS FIRST or NULLS LAST"),
        (
            "SELECT ROW_NUMBER() OVER (ORDER BY updated_at NULLS LAST) FROM `portable.raw.t`",
            "declared unique",
        ),
        (
            "SELECT id FROM `portable.raw.a` UNION DISTINCT SELECT id FROM `portable.raw.b`",
            "UNION ALL only",
        ),
        ("SELECT * FROM `portable.raw.a` NATURAL JOIN `portable.raw.b`", "NATURAL JOIN"),
        (
            "SELECT * FROM `portable.raw.a` LEFT SEMI JOIN `portable.raw.b` ON a.id = b.id",
            "join type is unsupported",
        ),
        ("SELECT SAFE_CAST(id AS INT64) FROM `portable.raw.t`", "TryCast"),
        (
            "SELECT CAST(amount AS NUMERIC(10, 2)) FROM `portable.raw.t`",
            r"DECIMAL\(38, 9\)",
        ),
        ("SELECT CAST(event_at AS TIMESTAMP) FROM `portable.raw.t`", "precision 6"),
        ("SELECT `MixedCase` FROM `portable.raw.t`", "must not be quoted"),
        ("SELECT MixedCase FROM `portable.raw.t`", "lowercase snake_case"),
        ("SELECT 'e\u0301' FROM `portable.raw.t`", "Unicode NFC"),
        ("SELECT CURRENT_TIMESTAMP() FROM `portable.raw.t`", "CurrentTimestamp"),
    ],
)
def test_portable_subset_rejects_ambiguous_or_provider_specific_sql(
    sql: str,
    message: str,
) -> None:
    with pytest.raises(PortableSqlError, match=message):
        parse_portable_query(sql)


def test_portable_render_rejects_portable_as_target() -> None:
    query = parse_portable_query("SELECT id FROM `portable.raw.t`")

    with pytest.raises(PortableSqlError, match="not a render target"):
        render_portable_query(query, target="portable")


def test_portable_relations_are_limited_to_declared_refs() -> None:
    sql = "SELECT id FROM `portable.raw.contacts`"

    with pytest.raises(PortableSqlError, match="outside its declared refs"):
        parse_portable_query(
            sql,
            allowed_relations={("portable", "raw", "accounts")},
        )


def test_portable_cte_may_reference_a_declared_relation() -> None:
    query = parse_portable_query(
        """
        WITH source AS (
          SELECT id FROM `portable.raw.contacts`
        )
        SELECT id FROM source
        """,
        allowed_relations={("portable", "raw", "contacts")},
    )

    assert isinstance(query, exp.Select)
