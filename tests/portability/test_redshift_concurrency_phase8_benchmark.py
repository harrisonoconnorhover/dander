"""Credential-free checks for the Phase 8 Redshift concurrency harness."""

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
from scripts.benchmarks import redshift_concurrency_phase8 as concurrency

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dander.concurrency import FencingToken
    from dander.warehouse import RelationRef, WarehouseRuntime


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-concurrency"


def _config(**overrides: object) -> concurrency.RedshiftConcurrencyConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-conc",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-conc-copy",
        "staging_bucket": "dander-p8q-rc31-rs-conc-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "rows_per_pipeline": 5_000,
        "payload_bytes": 3,
        "copy_part_rows": 5_000,
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return concurrency.RedshiftConcurrencyConfig(**values)  # type: ignore[arg-type]


def _identity() -> concurrency.CandidateIdentity:
    return concurrency.CandidateIdentity(
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


def _approval(config: concurrency.RedshiftConcurrencyConfig) -> concurrency._Approval:
    return concurrency._Approval(
        objectives=ApprovedObjectiveSet(
            names=concurrency._OBJECTIVES,
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
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


def _result(**overrides: object) -> concurrency._ConcurrencyResult:
    values: dict[str, object] = {
        "duration_ms": 500,
        "peak_rss_bytes": 128 * 1_024 * 1_024,
        "pipeline_duration_ms": 400,
        "pipeline_count": 4,
        "rows_per_pipeline": 5_000,
        "total_rows": 20_000,
        "logical_input_bytes": 540_000,
        "concurrent_claim_attempts": 2,
        "stale_publications_rejected": 1,
        "copy_operations": 4,
        "query_ids": ("101", "102", "103", "104"),
        "queue_duration_ms": 3,
        "load_duration_ms": 350,
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
    return concurrency._ConcurrencyResult(**values)  # type: ignore[arg-type]


def _manifest(config: concurrency.RedshiftConcurrencyConfig) -> dict[str, object]:
    assert shared.__file__ is not None
    assert bulk.__file__ is not None
    assert concurrency.__file__ is not None
    return {
        "schema": concurrency._APPROVAL_SCHEMA,
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
                **copy.deepcopy(concurrency._FARGATE_LAUNCHER_REQUIREMENTS),
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
            "task_role": copy.deepcopy(concurrency._TASK_ROLE_REQUIREMENTS),
            "execution": {
                "harness_sha256": concurrency._file_sha256(Path(concurrency.__file__)),
                "shared_harness_sha256": concurrency._file_sha256(Path(shared.__file__)),
                "bulk_harness_sha256": concurrency._file_sha256(Path(bulk.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
                "cost_observation_delay_seconds": config.cost_observation_delay_seconds,
                "candidate_command": concurrency._CANDIDATE_COMMAND,
            },
        },
        "approved_objectives": {
            "names": list(concurrency._OBJECTIVES),
            "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
            "profile_id": "aws_native_redshift",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def test_report_records_exact_concurrency_cost_and_zero_retries() -> None:
    config = _config()

    payload = json.loads(
        concurrency._report(config, _identity(), _approval(config), _result()).to_json()
    )

    assert payload["status"] == "passed"
    assert payload["workload"]["input_rows"] == 20_000
    assert payload["workload"]["concurrency"] == 4
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
    assert metrics["concurrent_claim_attempts"] == "2"
    assert metrics["readback_rows"] == "20000"
    assert metrics["provider_operation_retries"] == "0"
    assert metrics["stale_publications_rejected"] == "1"


def test_exact_readback_query_covers_all_four_independent_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    captured: dict[str, object] = {}

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield object()

    def fake_execute(_connection: object, statement: str, *, fetch: str) -> object:
        captured.update(statement=statement, fetch=fetch)
        rows = tuple((index, 5_000, 5_000, 3, 3) for index in range(4))
        return SimpleNamespace(rows=rows)

    monkeypatch.setattr(concurrency, "open_connection", fake_open_connection)
    monkeypatch.setattr(concurrency, "execute", fake_execute)
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    concurrency._require_independent_readback(
        cast("WarehouseRuntime", object()), config=config, schema="owned_schema"
    )

    statement = cast("str", captured["statement"])
    assert "COUNT(*) AS row_count" in statement
    assert " AS rows" not in statement
    assert statement.count("pipeline_index") == 7
    for index in range(4):
        assert f'FROM "owned_schema"."pipeline_{index:02d}_records"' in statement


def test_readback_rejects_any_pipeline_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()

    @contextmanager
    def fake_open_connection(_factory: object) -> Iterator[object]:
        yield object()

    monkeypatch.setattr(concurrency, "open_connection", fake_open_connection)
    monkeypatch.setattr(
        concurrency,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(
            rows=((0, 5_000, 5_000, 3, 3), (1, 4_999, 4_999, 3, 3))
        ),
    )
    monkeypatch.setattr(bulk, "_connection_factory", lambda _runtime: object())

    with pytest.raises(
        concurrency.RedshiftConcurrencyQualificationError,
        match="readback differs",
    ):
        concurrency._require_independent_readback(
            cast("WarehouseRuntime", object()), config=config, schema="owned_schema"
        )


def test_independent_pipeline_fences_exist_before_threaded_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    claims: list[tuple[RelationRef, FencingToken]] = []
    writes: list[tuple[str, str]] = []

    class FakeFence:
        def claim(self, relation: RelationRef, token: FencingToken) -> object:
            claims.append((relation, token))
            return object()

    def fake_write_table(
        _runtime: object,
        *,
        config: object,
        schema: str,
        table: str,
        pipeline_id: str,
        rows: int,
        payload_bytes: int,
    ) -> tuple[int, int, tuple[object, ...]]:
        del config, payload_bytes
        assert len(claims) == 4
        writes.append((schema, table))
        return rows, 1, ()

    runtime = SimpleNamespace(target_fence=FakeFence())
    monkeypatch.setattr(bulk, "_write_table", fake_write_table)

    rows, operations = concurrency._write_independent_pipelines(
        cast("WarehouseRuntime", runtime), config=config, schema="owned_schema"
    )

    assert rows == 20_000
    assert operations == ()
    assert [relation.name for relation, _token in claims] == [
        f"pipeline_{index:02d}_records" for index in range(4)
    ]
    assert [token.pipeline_id for _relation, token in claims] == [
        f"phase8_redshift_concurrency_{index:02d}" for index in range(4)
    ]
    assert sorted(writes) == [
        ("owned_schema", f"pipeline_{index:02d}_records") for index in range(4)
    ]


def test_approval_binds_task_role_launcher_harness_and_zero_retries(tmp_path: Path) -> None:
    config = _config()
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(_manifest(config)), encoding="utf-8")

    approval = concurrency._load_approval(path, config=config, identity=_identity())

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")


def test_corrective_objective_binds_corrected_harness_and_same_rc31_workload() -> None:
    config = concurrency.RedshiftConcurrencyConfig(
        account_id="184463061564",
        host="dander-p8q-rc31-rs-conc.184463061564.us-east-1.redshift-serverless.amazonaws.com",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc31-rs-conc",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc31-rs-conc-redshift-copy"),
        staging_bucket="dander-p8q-rc31-rs-conc-184463061564-staging",
        staging_prefix="phase8/0.9.0rc31/staging",
    )
    identity = concurrency.CandidateIdentity(
        release_version="0.9.0rc31",
        git_commit="3d6a59484737bf1192f0389b8f93a3a24c780fc4",
        image_digest=("sha256:26dac10d6cd81eef15a96a26fb011c0266ed4de6e4e5b21f596185edd3c387c9"),
        approval_reference=(
            "codex-goal-02043c37-096e-416a-875c-b405c4af0594-"
            "aws-redshift-concurrency-corrective-usd-0.50"
        ),
        benchmark_date=date(2026, 8, 22),
        launcher="aws_step_functions_fargate",
        secret_provider="aws_task_role",
        service_shapes=("redshift_serverless_8_rpu", "dander_2cpu_4gib"),
        provider_job_ids=("namespace:pending", "workgroup:pending"),
    )

    approval = concurrency._load_approval(
        Path(
            "docs/evidence/phase8/2026-08-22/"
            "aws-native-rc31-redshift-concurrency-corrective-objectives.json"
        ),
        config=config,
        identity=identity,
    )

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")


@pytest.mark.parametrize(
    ("section", "field", "invalid_value"),
    [
        ("task_role", "required_global_actions", ["tag:GetResources"]),
        ("fargate_harness", "runtime_cpu_architecture", "X86_64"),
        ("fargate_harness", "candidate_cli_executable", "/app/.venv/bin/dander"),
        ("execution", "provider_operation_retries", 1),
    ],
)
def test_approval_rejects_unprotected_execution_boundary(
    tmp_path: Path,
    section: str,
    field: str,
    invalid_value: object,
) -> None:
    config = _config()
    manifest = _manifest(config)
    configuration = cast("dict[str, object]", manifest["configuration"])
    target = cast("dict[str, object]", configuration[section])
    target[field] = invalid_value
    path = tmp_path / "objective.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        concurrency._load_approval(path, config=config, identity=_identity())


def test_retry_environment_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    concurrency._require_no_provider_retries()

    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    with pytest.raises(concurrency.RedshiftConcurrencyQualificationError, match="exactly 1"):
        concurrency._require_no_provider_retries()
