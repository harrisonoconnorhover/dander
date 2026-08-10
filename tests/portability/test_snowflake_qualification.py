"""Credential-free checks for the opt-in Snowflake qualification harness."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.benchmarks import snowflake

from dander.providers.snowflake.session import SnowflakeStatementResult
from dander.warehouse import RelationRef, WarehouseRuntime


def _config(**overrides: object) -> snowflake.SnowflakeQualificationConfig:
    values: dict[str, object] = {
        "account": "org-account",
        "user": "DANDER_USER",
        "database": "DANDER_TEST",
        "warehouse": "DANDER_WH",
        "role": "DANDER_ROLE",
    }
    values.update(overrides)
    return snowflake.SnowflakeQualificationConfig(**values)  # type: ignore[arg-type]


def test_qualification_config_reuses_provider_validation_and_only_stores_references() -> None:
    config = _config(
        auth_method="oauth",
        token_env="DANDER_TEST_SNOWFLAKE_TOKEN",
    )

    values = snowflake._provider_values(
        config,
        direct=True,
        schema_name="DANDER_QUAL_TEST",
    )

    assert values["auth"] == {
        "method": "oauth",
        "token_env": "DANDER_TEST_SNOWFLAKE_TOKEN",
    }
    assert values["direct_max_rows"] == 2
    assert values["schema"] == "DANDER_QUAL_TEST"
    assert "token" not in values


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_method": "password"}, "auth_method"),
        ({"copy_part_rows": 0}, "copy_part_rows"),
        ({"token_env": "not-an-env", "auth_method": "oauth"}, "token_env"),
    ],
)
def test_qualification_config_fails_before_provider_io(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


def test_graph_fixture_preserves_native_database_and_schema_coordinates() -> None:
    source = RelationRef(
        catalog="DANDER_TEST",
        namespace="DANDER_QUAL_TEST",
        name="direct_records",
    )

    plan = snowflake._graph_plan(source=source, target_schema="DANDER_QUAL_TEST")

    assert plan.bindings.source_relations == {"records": source}
    assert plan.targets[0].target.relation_ref == RelationRef(
        catalog="DANDER_TEST",
        namespace="DANDER_QUAL_TEST",
        name="graph_records",
    )


def test_staging_residue_query_excludes_durable_load_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class _Connection:
        def close(self) -> None:
            return None

    def fake_execute(
        _connection: object,
        statement: str,
        _parameters: object = (),
        *,
        fetch: str = "none",
    ) -> SnowflakeStatementResult:
        statements.append(statement)
        assert fetch == "one"
        return SnowflakeStatementResult(rowcount=1, query_id=None, row=(0,))

    runtime = cast(
        "WarehouseRuntime",
        SimpleNamespace(
            target_fence=SimpleNamespace(connection_factory=lambda: _Connection()),
        ),
    )
    monkeypatch.setattr(snowflake, "execute", fake_execute)

    assert snowflake._staging_residue(runtime, "DANDER_TEST", "DANDER_QUAL_TEST") == (0, 0)
    assert "REGEXP_LIKE(TABLE_NAME, '^dander_stage_[0-9a-f]{20}$')" in statements[0]
    assert "DANDER_STAGE_LOADS" not in statements[0]


def test_cli_failure_record_never_exposes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "provider-secret-response"

    def fail(_config: snowflake.SnowflakeQualificationConfig) -> None:
        raise snowflake.SnowflakeQualificationError(secret)

    monkeypatch.setattr(snowflake, "run_snowflake_qualification", fail)

    exit_code = snowflake.main(
        [
            "--account",
            "org-account",
            "--user",
            "DANDER_USER",
            "--database",
            "DANDER_TEST",
            "--warehouse",
            "DANDER_WH",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["qualification_status"] == "failed"
    assert secret not in json.dumps(payload)
