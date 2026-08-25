"""Credential-free checks for the Phase 8 Redshift bulk harness."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.writer import WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-bulk"


def _config(**overrides: object) -> bulk.RedshiftBulkConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-bk",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-bk-redshift-copy",
        "staging_bucket": "dander-p8q-rc31-rs-bk-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "narrow_rows": 4,
        "wide_rows": 2,
        "copy_part_rows": 2,
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return bulk.RedshiftBulkConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 22),
        launcher="docker_local",
        secret_provider="aws_sso_profile",
        service_shapes=("redshift_serverless_8_rpu", "dander_2cpu_2gib"),
        provider_job_ids=("namespace:namespace-id", "workgroup:workgroup-id"),
    )


def _approval(config: bulk.RedshiftBulkConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bulk._OBJECTIVES,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
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


def _result(**overrides: object) -> bulk._BulkResult:
    values: dict[str, object] = {
        "duration_ms": 200,
        "peak_rss_bytes": 128 * 1024 * 1024,
        "narrow_duration_ms": 125,
        "narrow_rows": 4,
        "narrow_logical_bytes": 224,
        "wide_duration_ms": 75,
        "wide_rows": 2,
        "wide_logical_bytes": 2096,
        "copy_operations": 2,
        "query_ids": ("101", "102"),
        "queue_duration_ms": 3,
        "load_duration_ms": 180,
        "bytes_processed": 4096,
        "spill_bytes": 0,
        "charged_seconds": Decimal("480"),
        "compute_seconds": Decimal("478.5"),
        "maximum_compute_capacity_rpu": Decimal("8"),
        "provider_cost_usd": Decimal("0.05"),
        "staging_tables": 0,
        "staging_objects": 0,
        "cleanup_verified": True,
    }
    values.update(overrides)
    return bulk._BulkResult(**values)  # type: ignore[arg-type]


def _manifest(config: bulk.RedshiftBulkConfig) -> dict[str, object]:
    assert shared.__file__ is not None
    return {
        "schema": bulk._APPROVAL_SCHEMA,
        "cost_ceiling": {
            "amount_usd": "0.50",
            "approval_reference": _REFERENCE,
        },
        "workload": config.workload_payload(),
        "configuration": {
            "fargate_harness": {
                **copy.deepcopy(bulk._FARGATE_LAUNCHER_REQUIREMENTS),
            },
            "redshift": {
                "account_id": config.account_id,
                "region": config.region,
                "workgroup_name": config.workgroup_name,
                "host": config.host,
                "database": config.database,
                "copy_role_arn": config.copy_role_arn,
                "staging_bucket": config.staging_bucket,
                "staging_prefix": config.staging_prefix,
                "on_demand_rate_usd_per_rpu_hour": str(config.on_demand_rate_usd_per_rpu_hour),
            },
            "task_role": copy.deepcopy(bulk._TASK_ROLE_REQUIREMENTS),
            "execution": {
                "harness_sha256": bulk._file_sha256(Path(bulk.__file__)),
                "shared_harness_sha256": bulk._file_sha256(Path(shared.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
                "cost_observation_delay_seconds": config.cost_observation_delay_seconds,
                "defer_provider_cost_attribution": True,
                "candidate_command": bulk._CANDIDATE_COMMAND,
            },
        },
        "approved_objectives": {
            "names": list(bulk._OBJECTIVES),
            "benchmark_class": BenchmarkClass.BULK_THROUGHPUT.value,
            "profile_id": "aws_native_redshift",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def test_report_records_provider_measured_cost_and_zero_retries() -> None:
    config = _config()

    report = bulk._report(config, _identity(), _approval(config), _result())
    payload = json.loads(report.to_json())

    assert payload["status"] == "passed"
    assert payload["performance"]["costs"] == [
        {
            "amount": "0.05",
            "currency": "USD",
            "estimated": False,
            "provider": "aws",
            "service": "redshift_serverless",
        }
    ]
    metrics = {item["name"]: item["value"] for item in payload["performance"]["measurements"]}
    assert metrics["charged_seconds"] == "480"
    assert metrics["provider_operation_retries"] == "0"
    assert metrics["staging_objects"] == "0"
    assert payload["context"]["provider_job_ids"] == [
        "101",
        "102",
        "namespace:namespace-id",
        "workgroup:workgroup-id",
    ]


def test_deferred_cost_interim_round_trips_into_final_report(tmp_path: Path) -> None:
    config = _config()
    identity = _identity()
    approval = _approval(config)
    result = _result()
    interim = bulk._deferred_cost_interim_payload(
        schema=bulk._INTERIM_SCHEMA,
        configuration_sha256=config.configuration_sha256(),
        identity=identity,
        approval=approval,
        result=result,
    )
    path = tmp_path / "interim.json"
    path.write_text(json.dumps(interim), encoding="utf-8")

    loaded = bulk._load_deferred_cost_workload(
        path,
        schema=bulk._INTERIM_SCHEMA,
        configuration_sha256=config.configuration_sha256(),
        identity=identity,
        approval=approval,
        result_type=bulk._BulkResult,
    )
    finalized = bulk._with_external_cost(
        loaded,
        charged_seconds=Decimal("480"),
        compute_seconds=Decimal("478.5"),
        maximum_compute_capacity_rpu=Decimal("8"),
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )

    assert bulk._report(config, identity, approval, finalized).status.value == "passed"
    assert finalized.provider_cost_usd == Decimal("0.050000000000")


def test_deferred_cost_interim_requires_verified_cleanup(tmp_path: Path) -> None:
    config = _config()
    interim = bulk._deferred_cost_interim_payload(
        schema=bulk._INTERIM_SCHEMA,
        configuration_sha256=config.configuration_sha256(),
        identity=_identity(),
        approval=_approval(config),
        result=_result(),
    )
    workload = cast("dict[str, object]", interim["workload"])
    workload["cleanup_verified"] = False
    path = tmp_path / "interim.json"
    path.write_text(json.dumps(interim), encoding="utf-8")

    with pytest.raises(ValueError, match="cleanup was not verified|fields are incomplete"):
        bulk._load_deferred_cost_workload(
            path,
            schema=bulk._INTERIM_SCHEMA,
            configuration_sha256=config.configuration_sha256(),
            identity=_identity(),
            approval=_approval(config),
            result_type=bulk._BulkResult,
        )


def test_report_fails_closed_when_measured_cost_exceeds_ceiling() -> None:
    config = _config()

    report = bulk._report(
        config,
        _identity(),
        _approval(config),
        _result(provider_cost_usd=Decimal("0.500000000001")),
    )

    assert report.status.value == "failed"
    assert (
        next(
            objective for objective in report.objectives if objective.name == "cost_ceiling"
        ).status.value
        == "failed"
    )


def test_approval_binds_exact_harness_and_zero_provider_retries(tmp_path: Path) -> None:
    config = _config()
    manifest = _manifest(config)
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    approval = bulk._load_approval(path, config=config, identity=_identity())

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    cast("dict[str, object]", manifest["configuration"])["execution"] = {
        **cast(
            "dict[str, object]",
            cast("dict[str, object]", manifest["configuration"])["execution"],
        ),
        "provider_operation_retries": 1,
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="disable provider-operation retries"):
        bulk._load_approval(path, config=config, identity=_identity())


def test_retry_environment_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    bulk._require_no_provider_retries()

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    with pytest.raises(bulk.RedshiftBulkQualificationError, match="exactly 1"):
        bulk._require_no_provider_retries()


def test_approval_rejects_invalid_two_vcpu_fargate_memory(tmp_path: Path) -> None:
    config = _config()
    manifest = _manifest(config)
    fargate = cast(
        "dict[str, object]",
        cast("dict[str, object]", manifest["configuration"])["fargate_harness"],
    )
    fargate["task_memory_mib"] = 2_048
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="2-vCPU/4-GiB"):
        bulk._load_approval(path, config=config, identity=_identity())


def test_copy_telemetry_rejects_provider_operation_retry() -> None:
    operation = OperationTelemetry(
        provider="redshift",
        operation=TelemetryOperation.LOAD,
        retry_count=1,
        transport=WriteTransport.COPY,
    )

    with pytest.raises(bulk.RedshiftBulkQualificationError, match="provider-operation retry"):
        bulk._require_copy_operations((operation,))


def test_serverless_usage_reads_exact_provider_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    @contextmanager
    def connection(_factory: object) -> Iterator[object]:
        yield object()

    def execute_statement(
        _connection: object,
        statement: str,
        _parameters: object = (),
        *,
        fetch: str | None = None,
    ) -> object:
        statements.append(statement)
        assert fetch == "one"
        return SimpleNamespace(row=(480, 478.5, 8))

    monkeypatch.setattr(bulk, "open_connection", connection)
    monkeypatch.setattr(bulk, "execute", execute_statement)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    usage = bulk._serverless_usage(object())  # type: ignore[arg-type]

    assert usage == (Decimal("480"), Decimal("478.5"), Decimal("8"))
    assert len(statements) == 1
    assert "SUM(charged_seconds)" in statements[0]
    assert "SUM(compute_seconds)" in statements[0]
    assert "MAX(compute_capacity)" in statements[0]
