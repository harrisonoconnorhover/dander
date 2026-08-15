"""Credential-free checks for the opt-in Redshift qualification harness."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.benchmarks import redshift

from dander.ingestion import load_source_config
from dander.providers.redshift.runtime import RedshiftSchemaMapper
from dander.providers.redshift.session import RedshiftStatementResult
from dander.transform import SqlDialect, TransformProject
from dander.warehouse import RelationRef, WarehouseRuntime

_ROOT = Path(__file__).resolve().parents[2]


def _config(**overrides: object) -> redshift.RedshiftQualificationConfig:
    values: dict[str, object] = {
        "deployment": "provisioned",
        "host": "example.abc123.us-east-1.redshift.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "copy_role_arn": "arn:aws:iam::123456789012:role/DanderRedshiftCopy",
        "staging_bucket": "dander-redshift-staging",
        "cluster_identifier": "dander-test",
        "db_user": "dander_user",
    }
    values.update(overrides)
    return redshift.RedshiftQualificationConfig(**values)  # type: ignore[arg-type]


def test_qualification_config_reuses_provider_validation_and_stores_no_credentials() -> None:
    config = _config()

    values = redshift._provider_values(
        config,
        direct=True,
        schema_name="dander_qual_test",
        staging_prefix="dander/staging/qualification/test",
    )

    assert values["deployment"] == "provisioned"
    assert values["cluster_identifier"] == "dander-test"
    assert values["direct_max_logical_bytes"] == 1_024 * 1_024
    assert values["schema"] == "dander_qual_test"
    assert "password" not in values
    assert "access_key" not in values


def test_serverless_qualification_uses_workgroup_without_database_user() -> None:
    config = _config(
        deployment="serverless",
        workgroup_name="dander-test",
        cluster_identifier=None,
        db_user=None,
    )

    values = redshift._provider_values(
        config,
        direct=False,
        schema_name="dander_qual_test",
        staging_prefix="dander/staging/qualification/test",
    )

    assert values["workgroup_name"] == "dander-test"
    assert values["direct_max_rows"] == 0
    assert "cluster_identifier" not in values
    assert "db_user" not in values


def test_qualification_normalizes_its_owned_staging_prefix() -> None:
    config = _config(staging_prefix="/dander/staging/")

    assert config.staging_prefix == "dander/staging"


@pytest.mark.parametrize("wildcard", ["*", "?"])
def test_qualification_rejects_iam_wildcards_in_staging_prefix(wildcard: str) -> None:
    with pytest.raises(ValueError, match="safe non-empty S3 key prefix"):
        _config(staging_prefix=f"dander/{wildcard}/staging")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"db_user": None}, "check: <root>"),
        ({"direct_max_logical_bytes": 1_024 * 1_024 + 1}, "direct_max_logical_bytes"),
        ({"copy_part_rows": 0}, "copy_part_rows"),
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
        catalog="analytics",
        namespace="dander_qual_test",
        name="direct_records",
    )

    plan = redshift._graph_plan(source=source, target_schema="dander_qual_test")

    assert plan.bindings.source_relations == {"records": source}
    assert plan.targets[0].target.relation_ref == RelationRef(
        catalog="analytics",
        namespace="dander_qual_test",
        name="graph_records",
    )


def test_model_fixture_and_physical_source_use_the_same_relation(tmp_path: Path) -> None:
    redshift._write_model(
        tmp_path,
        "table_model",
        "table",
        "dander_qual_test",
    )

    project = TransformProject.load(
        tmp_path,
        catalog="analytics",
        raw_namespace="dander_qual_test",
        target_dialect=SqlDialect.REDSHIFT,
    )
    model = project.ordered(("table_model",))[0]

    assert '"analytics"."dander_qual_test"."records"' in project.compile(model)


def test_aws_profile_fixture_is_flat_and_compiles_for_redshift() -> None:
    source = load_source_config(_ROOT / "connectors" / "phase8_aws_fixture.yaml")
    (endpoint,) = source.endpoints

    schema = RedshiftSchemaMapper().canonical_schema(endpoint.canonical_raw_schema().fields)
    assert [(field.name, field.data_type.kind.value) for field in schema.fields] == [
        ("id", "integer"),
        ("title", "string"),
    ]

    project = TransformProject.load(
        _ROOT / "models",
        catalog="analytics",
        raw_namespace="raw",
        target_dialect=SqlDialect.REDSHIFT,
    )
    model = project.models["stg_phase8_aws__posts"]
    compiled = project.compile(model)

    assert 'FROM "analytics"."raw"."phase8_aws_fixture_posts"' in compiled
    assert "id AS post_id" in compiled


def test_staging_residue_query_excludes_durable_load_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class _Connection:
        autocommit = True

        def close(self) -> None:
            return None

    def fake_execute(
        _connection: object,
        statement: str,
        _parameters: object = (),
        *,
        fetch: str = "none",
    ) -> RedshiftStatementResult:
        statements.append(statement)
        assert fetch == "one"
        return RedshiftStatementResult(rowcount=1, row=(0,))

    runtime = cast(
        "WarehouseRuntime",
        SimpleNamespace(
            target_fence=SimpleNamespace(
                connection_factory=lambda: _Connection(),
                database="analytics",
            ),
        ),
    )
    monkeypatch.setattr(redshift, "execute", fake_execute)

    assert redshift._staging_table_count(runtime, "analytics") == 0
    assert "^dander_stage_[0-9a-f]{24}$" in statements[0]
    assert "dander_stage_loads" not in statements[0]
    assert "table_catalog = %s" in statements[0]
    assert "database_name" not in statements[0]


def test_s3_cleanup_deletes_only_the_owned_paginated_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "dander/staging/qualification/abc123"

    class _S3:
        def __init__(self) -> None:
            self.pages = 0
            self.deleted: list[dict[str, object]] = []

        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            self.pages += 1
            assert kwargs["Prefix"] == f"{prefix}/"
            if self.pages == 1:
                return {
                    "Contents": [{"Key": f"{prefix}/part-1.parquet"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "next",
                }
            assert kwargs["ContinuationToken"] == "next"
            return {
                "Contents": [{"Key": f"{prefix}/manifest.json"}],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs: object) -> dict[str, object]:
            self.deleted.append(dict(kwargs))
            return {}

    client = _S3()
    monkeypatch.setattr(redshift, "_s3", lambda _config: client)

    redshift._delete_prefix(_config(), prefix)

    assert client.deleted == [
        {
            "Bucket": "dander-redshift-staging",
            "Delete": {
                "Objects": [
                    {"Key": f"{prefix}/part-1.parquet"},
                    {"Key": f"{prefix}/manifest.json"},
                ]
            },
        }
    ]


def test_s3_cleanup_rejects_partial_delete_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "dander/staging/qualification/abc123"

    class _S3:
        def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
            return {
                "Contents": [{"Key": f"{prefix}/part-1.parquet"}],
                "IsTruncated": False,
            }

        def delete_objects(self, **_kwargs: object) -> dict[str, object]:
            return {"Errors": [{"Key": f"{prefix}/part-1.parquet", "Code": "AccessDenied"}]}

    monkeypatch.setattr(redshift, "_s3", lambda _config: _S3())

    with pytest.raises(
        redshift.RedshiftQualificationError,
        match="reported one or more undeleted objects",
    ):
        redshift._delete_prefix(_config(), prefix)


def test_failed_qualification_still_cleans_its_exact_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = cast("WarehouseRuntime", object())
    cleanup: list[tuple[str, str]] = []
    monkeypatch.setattr(redshift, "_warehouse_runtime", lambda *_args, **_kwargs: runtime)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise redshift.RedshiftQualificationError("sanitized failure")

    monkeypatch.setattr(redshift, "_run_profile", fail)
    monkeypatch.setattr(
        redshift,
        "_drop_schema",
        lambda _runtime, schema: cleanup.append(("schema", schema)),
    )
    monkeypatch.setattr(
        redshift,
        "_delete_prefix",
        lambda _config, prefix: cleanup.append(("prefix", prefix)),
    )

    with pytest.raises(redshift.RedshiftQualificationError, match="sanitized failure"):
        redshift.run_redshift_qualification(_config())

    assert [kind for kind, _value in cleanup] == ["schema", "prefix"]
    assert cleanup[0][1].startswith("dander_qual_")
    assert cleanup[1][1].startswith("dander/staging/qualification/")


def test_cli_failure_record_never_exposes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "provider-secret-response"

    def fail(_config: redshift.RedshiftQualificationConfig) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(redshift, "run_redshift_qualification", fail)

    exit_code = redshift.main(
        [
            "--deployment",
            "provisioned",
            "--host",
            "example.abc123.us-east-1.redshift.amazonaws.com",
            "--database",
            "analytics",
            "--region",
            "us-east-1",
            "--copy-role-arn",
            "arn:aws:iam::123456789012:role/DanderRedshiftCopy",
            "--staging-bucket",
            "dander-redshift-staging",
            "--cluster-identifier",
            "dander-test",
            "--db-user",
            "dander_user",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["qualification_status"] == "failed"
    assert secret not in json.dumps(payload)
