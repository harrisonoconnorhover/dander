"""Credential-free checks for the Phase 8 Redshift transform harness."""

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
from scripts.benchmarks import redshift_transform_phase8 as transform

from dander import __version__
from dander.providers.redshift.transform import RedshiftTransformRunner
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.transform import SqlDialect
from dander.writer import WriteField, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dander.warehouse import WarehouseRuntime


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-transform"


def _config(**overrides: object) -> transform.RedshiftTransformConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-xform",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-xform-copy",
        "staging_bucket": "dander-p8q-rc31-rs-xform-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return transform.RedshiftTransformConfig(**values)  # type: ignore[arg-type]


def _identity() -> transform.CandidateIdentity:
    return transform.CandidateIdentity(
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


def _approval(config: transform.RedshiftTransformConfig) -> transform._Approval:
    return transform._Approval(
        objectives=ApprovedObjectiveSet(
            names=transform._OBJECTIVES,
            benchmark_class=BenchmarkClass.TRANSFORM,
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


def _result(**overrides: object) -> transform._TransformResult:
    values: dict[str, object] = {
        "duration_ms": 1_000,
        "peak_rss_bytes": 128 * 1_024 * 1_024,
        "load_duration_ms": 400,
        "transform_duration_ms": 500,
        "input_rows": 100_100,
        "logical_input_bytes": 3_202_400,
        "output_rows": 100_001,
        "model_count": 4,
        "assertion_count": 21,
        "ownership_verifications": 22,
        "copy_operations": 2,
        "fenced_publications": 5,
        "query_ids": ("101", "102"),
        "queue_duration_ms": 3,
        "bytes_processed": 4_096,
        "spill_bytes": 0,
        "charged_seconds": Decimal("480"),
        "compute_seconds": Decimal("478.5"),
        "maximum_compute_capacity_rpu": Decimal("8"),
        "provider_cost_usd": Decimal("0.05"),
        "provider_operation_retries": 0,
        "staging_tables": 0,
        "staging_objects": 0,
        "cleanup_verified": True,
    }
    values.update(overrides)
    return transform._TransformResult(**values)  # type: ignore[arg-type]


def _manifest(config: transform.RedshiftTransformConfig) -> dict[str, object]:
    assert shared.__file__ is not None
    assert bulk.__file__ is not None
    assert transform.__file__ is not None
    return {
        "schema": transform._APPROVAL_SCHEMA,
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
                **copy.deepcopy(transform._FARGATE_LAUNCHER_REQUIREMENTS),
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
            "task_role": copy.deepcopy(transform._TASK_ROLE_REQUIREMENTS),
            "execution": {
                "harness_sha256": transform._file_sha256(Path(transform.__file__)),
                "shared_harness_sha256": transform._file_sha256(Path(shared.__file__)),
                "bulk_harness_sha256": transform._file_sha256(Path(bulk.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
                "cost_observation_delay_seconds": config.cost_observation_delay_seconds,
                "defer_provider_cost_attribution": True,
                "candidate_command": transform._CANDIDATE_COMMAND,
            },
        },
        "approved_objectives": {
            "names": list(transform._OBJECTIVES),
            "benchmark_class": BenchmarkClass.TRANSFORM.value,
            "profile_id": "aws_native_redshift",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def test_config_binds_exact_transform_shape() -> None:
    config = _config()

    assert config.workload_payload() == {
        "schema": "io.dander.phase8.redshift-transform/v1",
        "benchmark_class": "transform",
        "fact_rows": 100_000,
        "dimension_rows": 100,
        "delta_rows": 2,
        "models": ["scan", "join", "aggregation", "incremental_merge"],
        "generic_tests": ["accepted_values", "not_null", "unique"],
        "generic_assertions": 21,
        "copy_part_rows": 50_000,
        "copy_part_logical_bytes": 64 * 1_024 * 1_024,
    }
    with pytest.raises(ValueError, match="exactly 100000"):
        _config(fact_rows=99_999)
    with pytest.raises(ValueError, match="exactly 100"):
        _config(dimension_rows=99)


def test_models_compile_to_four_redshift_models_and_twenty_one_assertions(
    tmp_path: Path,
) -> None:
    transform._write_transform_models(tmp_path, target_schema="owned_target")
    runner = RedshiftTransformRunner(
        database="analytics",
        connection_factory=object(),  # type: ignore[arg-type]
        target_fence=object(),  # type: ignore[arg-type]
        statement_timeout_ms=900_000,
        raw_namespace="owned_source",
    )

    project, initial_models, _plans, initial_assertions = runner._preflight(  # noqa: SLF001
        tmp_path, selected=("aggregate_records", "incremental_records")
    )
    _project, replay_models, _plans, replay_assertions = runner._preflight(  # noqa: SLF001
        tmp_path, selected=("incremental_records",)
    )

    assert project.target_dialect is SqlDialect.REDSHIFT
    assert len(initial_models) == 4
    assert len(initial_assertions) + len(replay_assertions) + len(initial_assertions) == 21


def test_exact_readback_queries_cover_scan_join_aggregation_and_incremental(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    statements: list[str] = []
    expected_amount = sum(value % 17 for value in range(1, config.fact_rows + 1))
    rows = iter(
        (
            (config.fact_rows,),
            (config.fact_rows, 10),
            (10, config.fact_rows, expected_amount),
            (config.fact_rows,),
        )
    )

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield object()

    def fake_execute(_connection: object, statement: str, *, fetch: str) -> object:
        statements.append(statement)
        assert fetch == "one"
        return SimpleNamespace(row=next(rows), query_id=str(len(statements)))

    monkeypatch.setattr(transform, "open_connection", fake_open_connection)
    monkeypatch.setattr(transform, "execute", fake_execute)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    query_ids = transform._require_transform_initial(
        cast("WarehouseRuntime", object()), target_schema="owned_target", config=config
    )

    assert query_ids == ("1", "2", "3", "4")
    assert all('"owned_target"' in statement for statement in statements)
    assert any('SUM("row_count")' in statement for statement in statements)


def test_incremental_readback_uses_redshift_case_expressions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield object()

    def fake_execute(
        _connection: object,
        statement: str,
        parameters: tuple[int],
        *,
        fetch: str,
    ) -> object:
        captured.update(statement=statement, parameters=parameters, fetch=fetch)
        return SimpleNamespace(row=(100_001, 1, 1), query_id="result-query")

    monkeypatch.setattr(transform, "open_connection", fake_open_connection)
    monkeypatch.setattr(transform, "execute", fake_execute)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    query_ids = transform._require_transform_incremental(
        cast("WarehouseRuntime", object()),
        target_schema="owned_target",
        expected_rows=100_001,
    )

    assert query_ids == ("result-query",)
    assert cast("str", captured["statement"]).count("SUM(CASE WHEN") == 2
    assert "%s" in cast("str", captured["statement"])
    assert captured["parameters"] == (100_001,)


def test_source_seed_requires_copy_and_zero_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    operation = OperationTelemetry(
        provider="redshift",
        operation=TelemetryOperation.LOAD,
        duration_ms=1,
        rows_affected=100,
        transport=WriteTransport.COPY,
    )

    class FakeWriter:
        def write(self, records: object, _target: object) -> int:
            return len(tuple(cast("Iterator[dict[str, object]]", records)))

        def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
            return (operation,)

    runtime = SimpleNamespace(
        target_fence=SimpleNamespace(claim=lambda *_args: object()),
        writers=SimpleNamespace(build_ingestion_writer=lambda **_kwargs: FakeWriter()),
    )
    monkeypatch.setattr(transform, "RedshiftStagedWriter", FakeWriter)

    operations = transform._write_transform_source(
        cast("WarehouseRuntime", runtime),
        config=config,
        source_schema="owned_source",
        table="dimensions",
        pipeline_id="pipeline",
        business_key=("dimension_id",),
        fields=(WriteField(name="dimension_id", data_type="INT64"),),
        records=iter({"dimension_id": value} for value in range(100)),
        expected_rows=100,
    )

    assert operations == (operation,)


def test_approval_binds_task_role_launcher_harness_and_zero_retries(tmp_path: Path) -> None:
    config = _config()
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(_manifest(config)), encoding="utf-8")

    approval = transform._load_approval(path, config=config, identity=_identity())

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")


def test_report_records_exact_models_assertions_cost_and_cleanup() -> None:
    config = _config()

    payload = json.loads(
        transform._report(config, _identity(), _approval(config), _result()).to_json()
    )

    assert payload["status"] == "passed"
    assert payload["workload"]["input_rows"] == 100_100
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
    assert metrics["assertion_count"] == "21"
    assert metrics["model_count"] == "4"
    assert metrics["provider_operation_retries"] == "0"
    assert metrics["staging_objects"] == "0"
    assert metrics["staging_tables"] == "0"


def test_rc31_objective_does_not_authorize_changed_harness() -> None:
    config = transform.RedshiftTransformConfig(
        account_id="184463061564",
        host=("dander-p8q-rc31-rs-xform.184463061564.us-east-1.redshift-serverless.amazonaws.com"),
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc31-rs-xform",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc31-rs-xform-redshift-copy"),
        staging_bucket="dander-p8q-rc31-rs-xform-184463061564-staging",
        staging_prefix="phase8/0.9.0rc31/staging",
    )
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-aws-redshift-transform-usd-0.50"
    identity = transform.CandidateIdentity(
        release_version="0.9.0rc31",
        git_commit="3d6a59484737bf1192f0389b8f93a3a24c780fc4",
        image_digest=("sha256:26dac10d6cd81eef15a96a26fb011c0266ed4de6e4e5b21f596185edd3c387c9"),
        approval_reference=reference,
        benchmark_date=date(2026, 8, 22),
        launcher="aws_step_functions_fargate",
        secret_provider="aws_task_role",
        service_shapes=("redshift_serverless_8_rpu", "dander_2cpu_4gib"),
        provider_job_ids=("namespace:pending", "workgroup:pending"),
    )
    path = (
        Path(__file__).parents[2] / "docs/evidence/phase8/2026-08-22/"
        "aws-native-rc31-redshift-transform-objectives.json"
    )

    with pytest.raises(ValueError, match="protected harness"):
        transform._load_approval(path, config=config, identity=identity)
