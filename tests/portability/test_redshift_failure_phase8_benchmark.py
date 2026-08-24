"""Credential-free checks for the Phase 8 Redshift failure harness."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import psycopg
import pytest
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk
from scripts.benchmarks import redshift_failure_phase8 as failure

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.writer import WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-failure"


class _RedshiftDatabaseError(Exception):
    """Test double for redshift_connector.error.DatabaseError."""


def _config(**overrides: object) -> failure.RedshiftFailureConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-failure",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-failure-copy",
        "staging_bucket": "dander-p8q-rc31-rs-failure-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return failure.RedshiftFailureConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 22),
        launcher="aws_native_fargate",
        secret_provider="aws_task_role",
        service_shapes=("dander_2cpu_4gib", "redshift_serverless_8_rpu"),
        provider_job_ids=("task:test", "workgroup:test"),
    )


def _approval(config: failure.RedshiftFailureConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=failure._OBJECTIVES,
            benchmark_class=BenchmarkClass.FAILURE,
            profile_id="aws_native_redshift",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
        account_id=config.account_id,
        region=config.region,
        workgroup_name=config.workgroup_name,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        staging_prefix=config.staging_prefix,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def _operation() -> OperationTelemetry:
    return OperationTelemetry(
        provider="redshift",
        operation=TelemetryOperation.LOAD,
        duration_ms=10,
        rows_affected=1,
        transport=WriteTransport.COPY,
        query_id="query-1",
    )


def test_config_binds_only_the_four_failure_probes() -> None:
    config = _config()

    assert config.workload_payload() == {
        "schema": "io.dander.phase8.redshift-failure/v1",
        "benchmark_class": "failure",
        "probes": [
            "credential_rejection",
            "failed_copy_cleanup",
            "provider_operation_recovery",
            "stale_publication_rejection",
        ],
        "recovery_rows": 1,
        "copy_part_rows": 1,
        "copy_part_logical_bytes": 16 * 1_024 * 1_024,
    }
    with pytest.raises(ValueError, match="one-row COPY parts"):
        _config(copy_part_rows=2)


def test_run_rejects_failures_recovers_and_cleans(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    runtime = object()
    calls: list[str] = []
    dropped: list[str] = []
    deleted: list[str] = []

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    def build_runtime(*_args: object, **_kwargs: object) -> object:
        calls.append("runtime")
        return runtime

    def probe_credentials(_config: failure.RedshiftFailureConfig) -> int:
        calls.append("credential")
        return 5

    monkeypatch.setattr(bulk, "_warehouse_runtime", build_runtime)
    monkeypatch.setattr(
        failure,
        "_probe_failed_copy_cleanup_and_recovery",
        lambda *_args, **_kwargs: (7, 11, (_operation(),)),
    )
    monkeypatch.setattr(shared, "_exercise_concurrent_fence", lambda *_args: (True, 2))
    monkeypatch.setattr(bulk, "_staging_table_count", lambda *_args: 0)
    monkeypatch.setattr(
        bulk, "_serverless_usage", lambda _runtime: (Decimal("480"), Decimal("478.5"), Decimal("8"))
    )
    monkeypatch.setattr(shared, "_prefix_object_count", lambda *_args: 0)
    monkeypatch.setattr(shared, "_schema_exists", lambda *_args: False)
    monkeypatch.setattr(shared, "_drop_schema", lambda _runtime, schema: dropped.append(schema))
    monkeypatch.setattr(shared, "_delete_prefix", lambda _config, prefix: deleted.append(prefix))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    report = failure.run_phase8_redshift_failure(
        config,
        identity=_identity(),
        approval=_approval(config),
        credential_probe=probe_credentials,
    )
    payload = cast("dict[str, Any]", json.loads(report.to_json()))
    metrics = {
        item["name"]: item["value"]
        for item in cast("dict[str, Any]", payload["performance"])["measurements"]
    }

    assert payload["status"] == "passed"
    assert metrics["probe_count"] == "4"
    assert metrics["provider_operation_retries"] == "0"
    assert metrics["stale_publications_rejected"] == "1"
    assert calls == ["credential", "runtime"]
    assert dropped and deleted


def test_run_classifies_runtime_failure_after_credential_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    calls: list[str] = []
    deleted: list[str] = []

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    def probe_credentials(_config: failure.RedshiftFailureConfig) -> int:
        calls.append("credential")
        return 5

    def fail_runtime(*_args: object, **_kwargs: object) -> object:
        calls.append("runtime")
        raise RuntimeError("sensitive driver startup detail")

    monkeypatch.setattr(bulk, "_warehouse_runtime", fail_runtime)
    monkeypatch.setattr(shared, "_delete_prefix", lambda _config, prefix: deleted.append(prefix))

    with pytest.raises(
        failure.RedshiftFailureQualificationError,
        match="runtime construction failed after credential-rejection probe passed",
    ) as caught:
        failure.run_phase8_redshift_failure(
            config,
            identity=_identity(),
            approval=_approval(config),
            credential_probe=probe_credentials,
        )

    assert "sensitive driver startup detail" not in str(caught.value)
    assert calls == ["credential", "runtime"]
    assert deleted


def test_run_sanitizes_credential_probe_failure_before_owned_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    dropped: list[str] = []
    deleted: list[str] = []

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    monkeypatch.setattr(bulk, "_warehouse_runtime", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(shared, "_prefix_object_count", lambda *_args: 0)
    monkeypatch.setattr(shared, "_schema_exists", lambda *_args: False)
    monkeypatch.setattr(shared, "_drop_schema", lambda _runtime, schema: dropped.append(schema))
    monkeypatch.setattr(shared, "_delete_prefix", lambda _config, prefix: deleted.append(prefix))

    def fail_probe(_config: failure.RedshiftFailureConfig) -> int:
        raise RuntimeError("sensitive provider detail")

    with pytest.raises(
        failure.RedshiftFailureQualificationError,
        match="credential-rejection probe failed before owned mutation",
    ) as caught:
        failure.run_phase8_redshift_failure(
            config,
            identity=_identity(),
            approval=_approval(config),
            credential_probe=fail_probe,
        )

    assert "sensitive provider detail" not in str(caught.value)
    assert not dropped and not deleted


def test_run_reports_only_sanitized_stage_timing_and_exception_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    dropped: list[str] = []
    deleted: list[str] = []

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    monkeypatch.setattr(bulk, "_warehouse_runtime", lambda *_args, **_kwargs: object())

    def fail_probe(*_args: object, **_kwargs: object) -> tuple[int, int, tuple[object, ...]]:
        raise RuntimeError("sensitive provider detail")

    monkeypatch.setattr(failure, "_probe_failed_copy_cleanup_and_recovery", fail_probe)
    monkeypatch.setattr(shared, "_prefix_object_count", lambda *_args: 0)
    monkeypatch.setattr(shared, "_schema_exists", lambda *_args: False)
    monkeypatch.setattr(shared, "_drop_schema", lambda _runtime, schema: dropped.append(schema))
    monkeypatch.setattr(shared, "_delete_prefix", lambda _config, prefix: deleted.append(prefix))

    with pytest.raises(
        failure.RedshiftFailureQualificationError,
        match=(
            r"^stage=failed_copy_cleanup_and_recovery; elapsed_ms=\d+; "
            r"exception_class=RuntimeError$"
        ),
    ) as caught:
        failure.run_phase8_redshift_failure(
            config,
            identity=_identity(),
            approval=_approval(config),
            credential_probe=lambda _config: 5,
        )

    assert "sensitive provider detail" not in str(caught.value)
    assert dropped and deleted


def test_credential_probe_uses_rejected_password_and_zero_retry_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeServerless:
        def get_credentials(self, **kwargs: object) -> Mapping[str, object]:
            captured["request"] = kwargs
            return {"dbUser": "runtime-user", "dbPassword": "real-secret"}

    def reject_connect(**kwargs: object) -> object:
        captured["connect"] = kwargs
        raise psycopg.OperationalError("password authentication failed")

    monkeypatch.setattr(failure, "_aws_client", lambda *_args, **_kwargs: FakeServerless())
    monkeypatch.setattr(psycopg, "connect", reject_connect)

    assert failure._probe_credential_rejection(_config()) >= 1
    assert cast("dict[str, object]", captured["request"])["durationSeconds"] == 900
    assert cast("dict[str, object]", captured["connect"])["password"] != "real-secret"


def test_credential_probe_does_not_misclassify_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeServerless:
        def get_credentials(self, **_kwargs: object) -> Mapping[str, object]:
            return {"dbUser": "runtime-user", "dbPassword": "real-secret"}

    def reject_connect(**_kwargs: object) -> object:
        raise psycopg.OperationalError("connection timed out")

    monkeypatch.setattr(failure, "_aws_client", lambda *_args, **_kwargs: FakeServerless())
    monkeypatch.setattr(psycopg, "connect", reject_connect)

    with pytest.raises(
        failure.RedshiftFailureQualificationError,
        match="unexpected provider reason",
    ):
        failure._probe_credential_rejection(_config())


@pytest.mark.parametrize(
    "database_error",
    (psycopg.DatabaseError, _RedshiftDatabaseError),
)
def test_failed_copy_probe_uses_exact_sql_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
    database_error: type[Exception],
) -> None:
    config = _config()
    statements: list[str] = []
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeS3:
        def put_object(self, **kwargs: object) -> object:
            calls.append(("put", kwargs))
            return object()

        def delete_object(self, **kwargs: object) -> object:
            calls.append(("delete", kwargs))
            return object()

    class FakeConnection:
        def rollback(self) -> None:
            calls.append(("rollback", {}))

        def commit(self) -> None:
            calls.append(("commit", {}))

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield FakeConnection()

    def fake_execute(
        _connection: object,
        statement: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        statements.append(statement)
        if statement.startswith("COPY "):
            raise database_error("invalid parquet")
        return SimpleNamespace(row=(0,))

    monkeypatch.setattr(failure, "_aws_client", lambda *_args, **_kwargs: FakeS3())
    monkeypatch.setattr(failure, "_redshift_database_error", lambda: _RedshiftDatabaseError)
    monkeypatch.setattr(failure, "open_connection", fake_open_connection)
    monkeypatch.setattr(failure, "execute", fake_execute)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())
    monkeypatch.setattr(shared, "_prefix_object_count", lambda *_args: 0)
    monkeypatch.setattr(bulk, "_write_table", lambda *_args, **_kwargs: (1, 3, (_operation(),)))
    monkeypatch.setattr(bulk, "_require_table_shape", lambda *_args, **_kwargs: None)

    failed_ms, recovery_ms, operations = failure._probe_failed_copy_cleanup_and_recovery(
        config,
        cast("Any", object()),
        schema_name="owned_schema",
        staging_prefix="phase8/0.9.0rc31/staging/failure/run",
    )

    assert failed_ms >= 1 and recovery_ms >= 1
    assert operations == (_operation(),)
    assert statements[:2] == [
        'CREATE SCHEMA IF NOT EXISTS "owned_schema"',
        'CREATE TABLE "owned_schema"."failed_copy_records" '
        '("id" VARCHAR(32) NOT NULL, "payload" VARCHAR(64) NOT NULL)',
    ]
    assert any("FORMAT AS PARQUET" in statement for statement in statements)
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in statements)
    assert [name for name, _kwargs in calls] == [
        "put",
        "commit",
        "rollback",
        "commit",
        "delete",
    ]


def test_historical_rc31_objective_preserves_old_harness_binding(tmp_path: Path) -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-aws-redshift-failure-usd-0.50"
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc31-rs-failure",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc31-rs-failure-redshift-copy"),
        staging_bucket="dander-p8q-rc31-rs-failure-184463061564-staging",
        staging_prefix="phase8/0.9.0rc31/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc31",
        git_commit="3d6a59484737bf1192f0389b8f93a3a24c780fc4",
        image_digest=("sha256:26dac10d6cd81eef15a96a26fb011c0266ed4de6e4e5b21f596185edd3c387c9"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-23/aws-native-rc31-redshift-failure-rebound-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        failure._load_approval(manifest, config=config, identity=identity)

    assert payload["configuration"]["execution"]["harness_sha256"] == (
        "25ecf4691b4e1e2e5a71e34b2bed7de73f888721dc98b1b4a25fd27e3cda6e1a"
    )
    assert payload["configuration"]["fargate_harness"]["state_machine_retry_states"] == 0


def test_historical_rc32_objective_preserves_failed_harness_binding(
    tmp_path: Path,
) -> None:
    reference = "codex-user-2026-08-24-additional-phase8-usd-10-redshift-failure-usd-0.50"
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-failure",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc32-rs-failure-redshift-copy"),
        staging_bucket="dander-p8q-rc32-rs-failure-184463061564-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc32",
        git_commit="0d648a622fa2b0240a3b7b5fb8b7151445591bca",
        image_digest=("sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        failure._load_approval(manifest, config=config, identity=identity)

    assert payload["configuration"]["execution"]["harness_sha256"] == (
        "25ecf4691b4e1e2e5a71e34b2bed7de73f888721dc98b1b4a25fd27e3cda6e1a"
    )
    assert payload["budget_allocation"]["aggregate_ceiling_usd"] == "20.00"
    assert payload["configuration"]["execution"]["corrective_candidate_executions"] == 1
    assert payload["configuration"]["execution"]["provider_operation_retries"] == 0
    assert payload["configuration"]["fargate_harness"]["state_machine_retry_states"] == 0


def test_historical_rc32_harness_corrective_objective_preserves_launcher_boundary() -> None:
    reference = (
        "codex-user-2026-08-24-additional-phase8-usd-10-"
        "redshift-failure-harness-corrective-usd-0.50"
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-harness-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))

    assert payload["cost_ceiling"]["approval_reference"] == reference
    assert payload["budget_allocation"]["aggregate_ceiling_usd"] == "20.00"
    assert payload["configuration"]["execution"]["corrective_candidate_executions"] == 1
    assert payload["configuration"]["execution"]["provider_operation_retries"] == 0
    assert payload["configuration"]["fargate_harness"]["state_machine_retry_states"] == 0
    assert payload["configuration"]["execution"]["candidate_command"] == (
        "dander qualification-run /tmp/harness/scripts/benchmarks/redshift_failure_phase8.py"
    )
    assert "harness_working_directory" not in payload["configuration"]["fargate_harness"]


def test_historical_rc32_launcher_corrective_objective_preserves_missing_pythonpath(
    tmp_path: Path,
) -> None:
    reference = (
        "codex-user-2026-08-24-redshift-diagnosis-runs-"
        "redshift-failure-launcher-corrective-usd-0.50"
    )
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-fail-c3",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc32-rs-fail-c3-redshift-copy"),
        staging_bucket="dander-p8q-rc32-rs-fail-c3-184463061564-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc32",
        git_commit="0d648a622fa2b0240a3b7b5fb8b7151445591bca",
        image_digest=("sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-launcher-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        failure._load_approval(manifest, config=config, identity=identity)

    assert payload["configuration"]["execution"]["harness_sha256"] == (
        "5e0eadd77f64c0b3bf94370127ce8fef7f98187184ed9bd1bd44d290842a4c8d"
    )
    assert payload["budget_allocation"]["aggregate_ceiling_usd"] == "20.00"
    assert payload["configuration"]["execution"]["corrective_candidate_executions"] == 1
    assert payload["configuration"]["execution"]["provider_operation_retries"] == 0
    harness = payload["configuration"]["fargate_harness"]
    assert harness["state_machine_retry_states"] == 0
    assert harness["harness_working_directory"] == "/tmp/harness"
    assert harness["harness_import_root"] == "/tmp/harness"
    assert "harness_environment" not in harness
    assert payload["configuration"]["execution"]["candidate_command"] == (
        "cd /tmp/harness && dander qualification-run scripts/benchmarks/redshift_failure_phase8.py"
    )
    assert "PYTHONPATH" not in payload["configuration"]["execution"]["candidate_command"]


def test_historical_rc32_pythonpath_objective_preserves_import_environment(
    tmp_path: Path,
) -> None:
    reference = (
        "codex-user-2026-08-24-redshift-diagnosis-runs-"
        "redshift-failure-pythonpath-corrective-usd-0.50"
    )
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-fail-c4",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc32-rs-fail-c4-redshift-copy"),
        staging_bucket="dander-p8q-rc32-rs-fail-c4-184463061564-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc32",
        git_commit="0d648a622fa2b0240a3b7b5fb8b7151445591bca",
        image_digest=("sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-pythonpath-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        failure._load_approval(manifest, config=config, identity=identity)

    assert payload["configuration"]["execution"]["harness_sha256"] == (
        "5e0eadd77f64c0b3bf94370127ce8fef7f98187184ed9bd1bd44d290842a4c8d"
    )
    assert payload["budget_allocation"]["aggregate_ceiling_usd"] == "20.00"
    assert payload["configuration"]["execution"]["corrective_candidate_executions"] == 1
    assert payload["configuration"]["execution"]["provider_operation_retries"] == 0
    harness = payload["configuration"]["fargate_harness"]
    assert harness["state_machine_retry_states"] == 0
    assert harness["harness_working_directory"] == "/tmp/harness"
    assert harness["harness_import_root"] == "/tmp/harness"
    assert harness["harness_environment"] == {"PYTHONPATH": "/tmp/harness"}
    assert payload["configuration"]["execution"]["candidate_command"] == (
        "cd /tmp/harness && PYTHONPATH=/tmp/harness dander qualification-run "
        "scripts/benchmarks/redshift_failure_phase8.py"
    )


def test_historical_rc32_stage_diagnostic_objective_preserves_previous_harness_binding(
    tmp_path: Path,
) -> None:
    reference = (
        "codex-user-2026-08-24-redshift-diagnosis-runs-"
        "redshift-failure-stage-diagnostic-corrective-usd-0.50"
    )
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-fail-c5",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc32-rs-fail-c5-redshift-copy"),
        staging_bucket="dander-p8q-rc32-rs-fail-c5-184463061564-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc32",
        git_commit="0d648a622fa2b0240a3b7b5fb8b7151445591bca",
        image_digest=("sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-stage-diagnostic-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        failure._load_approval(manifest, config=config, identity=identity)

    assert payload["configuration"]["execution"]["harness_sha256"] == (
        "6a55fac4b4e6ba7519347918e997e634e7d4bfe3fd97a3dfbc5a88f27a297cf6"
    )
    assert payload["budget_allocation"]["aggregate_ceiling_usd"] == "20.00"
    execution = payload["configuration"]["execution"]
    assert execution["corrective_candidate_executions"] == 1
    assert execution["provider_operation_retries"] == 0
    assert execution["failure_diagnostic_contract"] == {
        "fields": ["stage", "elapsed_ms", "exception_class"],
        "provider_exception_messages": False,
        "candidate_exit_codes_accepted": [0],
    }
    harness = payload["configuration"]["fargate_harness"]
    assert harness["state_machine_retry_states"] == 0
    assert harness["harness_environment"] == {"PYTHONPATH": "/tmp/harness"}


def test_rc32_schema_corrective_objective_binds_schema_ready_harness_and_zero_retries(
    tmp_path: Path,
) -> None:
    reference = (
        "codex-user-2026-08-24-redshift-diagnosis-runs-redshift-failure-schema-corrective-usd-0.50"
    )
    config = failure.RedshiftFailureConfig(
        account_id="184463061564",
        host="private-host",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-fail-c6",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc32-rs-fail-c6-redshift-copy"),
        staging_bucket="dander-p8q-rc32-rs-fail-c6-184463061564-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc32",
        git_commit="0d648a622fa2b0240a3b7b5fb8b7151445591bca",
        image_digest=("sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-failure-schema-corrective-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    approval = failure._load_approval(manifest, config=config, identity=identity)

    assert approval.objectives.benchmark_class is BenchmarkClass.FAILURE
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")
    assert payload["budget_allocation"] == {
        "authorization_reference": (
            "codex-user-2026-08-24-redshift-diagnosis-runs-and-additional-usd-10"
        ),
        "aggregate_ceiling_usd": "20.00",
        "provider_measured_or_conservative_before_objective_usd": "8.491419762794",
        "existing_reserved_before_objective_usd": "4.00",
        "objective_reservation_usd": "0.50",
        "remaining_aggregate_ceiling_after_full_objective_reservation_usd": "7.008580237206",
    }
    execution = payload["configuration"]["execution"]
    assert execution["harness_sha256"] == (
        "cfd473736e171f7ee6a2a7de986f13b78c570af562c7ca5fb2a92579eeac3def"
    )
    assert execution["corrective_candidate_executions"] == 1
    assert execution["provider_operation_retries"] == 0
    assert payload["configuration"]["fargate_harness"]["state_machine_retry_states"] == 0
    prior = payload["configuration"]["prior_rc32_stage_diagnostic_attempt"]
    assert prior["sanitized_stage"] == "failed_copy_cleanup_and_recovery"
    assert prior["exception_class"] == "ProgrammingError"
    assert prior["owned_workload_cleanup_passed"] is True
