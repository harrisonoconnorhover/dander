"""Credential-free checks for the Phase 8 Redshift incremental harness."""

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
from scripts.benchmarks import redshift_incremental_phase8 as incremental

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dander.warehouse import WarehouseRuntime


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-incremental"


def _config(**overrides: object) -> incremental.RedshiftIncrementalConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-incr",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-incr-copy",
        "staging_bucket": "dander-p8q-rc31-rs-incr-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "seed_rows": 200,
        "delta_rows": 2,
        "payload_bytes": 3,
        "copy_part_rows": 2,
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return incremental.RedshiftIncrementalConfig(**values)  # type: ignore[arg-type]


def _identity() -> incremental.CandidateIdentity:
    return incremental.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 22),
        launcher="aws_step_functions_fargate",
        secret_provider="aws_task_role",
        service_shapes=("redshift_serverless_8_rpu", "dander_2cpu_4gib"),
        provider_job_ids=("namespace:namespace-id", "workgroup:workgroup-id"),
    )


def _approval(config: incremental.RedshiftIncrementalConfig) -> incremental._Approval:
    return incremental._Approval(
        objectives=ApprovedObjectiveSet(
            names=incremental._OBJECTIVES,
            benchmark_class=BenchmarkClass.INCREMENTAL,
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


def _result(**overrides: object) -> incremental._IncrementalResult:
    values: dict[str, object] = {
        "duration_ms": 400,
        "peak_rss_bytes": 128 * 1_024 * 1_024,
        "seed_duration_ms": 300,
        "seed_rows": 200,
        "seed_logical_bytes": 7_000,
        "delta_duration_ms": 100,
        "delta_rows": 2,
        "delta_logical_bytes": 70,
        "final_rows": 201,
        "updated_rows": 1,
        "inserted_rows": 1,
        "cursor_initial": 1,
        "cursor_final": 2,
        "cursor_regressions_rejected": 1,
        "regression_rows_affected": 0,
        "copy_operations": 2,
        "query_ids": ("101", "102"),
        "queue_duration_ms": 3,
        "load_duration_ms": 350,
        "bytes_processed": 4_096,
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
    return incremental._IncrementalResult(**values)  # type: ignore[arg-type]


def _manifest(config: incremental.RedshiftIncrementalConfig) -> dict[str, object]:
    assert shared.__file__ is not None
    assert bulk.__file__ is not None
    assert incremental.__file__ is not None
    return {
        "schema": incremental._APPROVAL_SCHEMA,
        "cost_ceiling": {"amount_usd": "0.50", "approval_reference": _REFERENCE},
        "workload": config.workload_payload(),
        "configuration": {
            "fargate_harness": {
                "task_cpu_units": 2_048,
                "task_memory_mib": 4_096,
                "task_timeout_seconds": 900,
                "cluster_executions": 1,
                "state_machine_executions": 1,
                "state_machine_retry_states": 0,
                "ecs_task_retries": 0,
                "container_restarts": 0,
                "automatic_retry": False,
            },
            "redshift": {
                "account_id": config.account_id,
                "region": config.region,
                "workgroup_name": config.workgroup_name,
                "copy_role_arn": config.copy_role_arn,
                "staging_bucket": config.staging_bucket,
                "staging_prefix": config.staging_prefix,
                "on_demand_rate_usd_per_rpu_hour": str(config.on_demand_rate_usd_per_rpu_hour),
            },
            "task_role": copy.deepcopy(incremental._TASK_ROLE_REQUIREMENTS),
            "execution": {
                "harness_sha256": incremental._file_sha256(Path(incremental.__file__)),
                "shared_harness_sha256": incremental._file_sha256(Path(shared.__file__)),
                "bulk_harness_sha256": incremental._file_sha256(Path(bulk.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
                "cost_observation_delay_seconds": config.cost_observation_delay_seconds,
            },
        },
        "approved_objectives": {
            "names": list(incremental._OBJECTIVES),
            "benchmark_class": BenchmarkClass.INCREMENTAL.value,
            "profile_id": "aws_native_redshift",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def test_report_records_exact_incremental_result_cost_and_zero_retries() -> None:
    config = _config()

    report = incremental._report(config, _identity(), _approval(config), _result())
    payload = json.loads(report.to_json())

    assert payload["status"] == "passed"
    assert payload["workload"]["input_rows"] == 2
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
    assert metrics["cursor_regressions_rejected"] == "1"
    assert metrics["final_target_rows"] == "201"
    assert metrics["provider_operation_retries"] == "0"
    assert metrics["staging_objects"] == "0"


def test_cursor_regression_is_rejected_before_provider_mutation() -> None:
    cursor = incremental._advance_cursor(None, 1)
    cursor = incremental._advance_cursor(cursor, 2)

    with pytest.raises(
        incremental.RedshiftIncrementalQualificationError,
        match="before provider mutation",
    ):
        incremental._advance_cursor(cursor, 1)


def test_records_are_exact_half_updates_and_half_inserts() -> None:
    config = _config()

    assert list(incremental._seed_records(config))[:2] == [
        {"id": "000000000000", "payload": "sss", "cursor_value": 1},
        {"id": "000000000001", "payload": "sss", "cursor_value": 1},
    ]
    assert list(incremental._delta_records(config)) == [
        {"id": "000000000000", "payload": "ddd", "cursor_value": 2},
        {"id": "000000000200", "payload": "ddd", "cursor_value": 2},
    ]


def test_exact_readback_query_uses_row_count_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    captured: dict[str, object] = {}

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield object()

    def fake_execute(
        _connection: object,
        statement: str,
        parameters: tuple[object, ...],
        *,
        fetch: str,
    ) -> object:
        captured.update(statement=statement, parameters=parameters, fetch=fetch)
        return SimpleNamespace(row=(201, 201, 1, 1, 2, 199, 0))

    monkeypatch.setattr(incremental, "open_connection", fake_open_connection)
    monkeypatch.setattr(incremental, "execute", fake_execute)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    assert incremental._require_incremental_result(
        cast("WarehouseRuntime", object()),
        config=config,
        schema="owned_schema",
        expected_rows=201,
    ) == (1, 1)
    statement = cast("str", captured["statement"])
    assert "COUNT(*) AS row_count" in statement
    assert " AS rows" not in statement
    assert 'FROM "owned_schema"."incremental_records"' in statement
    assert captured["parameters"] == (1, 200, "ddd", "sss")


def test_approval_binds_task_role_harness_and_zero_retries(tmp_path: Path) -> None:
    config = _config()
    manifest = _manifest(config)
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    approval = incremental._load_approval(path, config=config, identity=_identity())

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")


def test_approval_rejects_missing_global_tag_read(tmp_path: Path) -> None:
    config = _config()
    manifest = _manifest(config)
    configuration = cast("dict[str, object]", manifest["configuration"])
    task_role = cast("dict[str, object]", configuration["task_role"])
    task_role["required_global_actions"] = ["tag:GetResources"]
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="task-role access"):
        incremental._load_approval(path, config=config, identity=_identity())


def test_retry_environment_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    incremental._require_no_provider_retries()

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    with pytest.raises(incremental.RedshiftIncrementalQualificationError, match="exactly 1"):
        incremental._require_no_provider_retries()
