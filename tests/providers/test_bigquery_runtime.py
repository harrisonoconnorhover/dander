"""BigQuery's first provider-selected warehouse composition."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.cloud import bigquery

from dander.concurrency import FencingToken
from dander.providers import ProviderKind, default_provider_registry
from dander.telemetry import TelemetryOperation
from dander.warehouse import LogicalTypeKind, RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTransport

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _runtime() -> WarehouseRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {"provider": "bigquery", "location": "US"},
    )
    built = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"project": "unit-project"},
    )
    assert isinstance(built, WarehouseRuntime)
    return built


def test_default_registry_loads_bigquery_runtime_only_after_selection() -> None:
    module_name = "dander.providers.bigquery.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()

    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {"provider": "bigquery", "location": "EU"},
    )

    assert module_name not in sys.modules
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"project": "unit-project"},
    )
    assert isinstance(runtime, WarehouseRuntime)
    assert module_name in sys.modules
    assert runtime.capabilities.write_modes == frozenset(WriteMode)
    assert runtime.capabilities.transports == frozenset(WriteTransport)


def test_bigquery_runtime_exposes_codec_schema_fence_and_telemetry() -> None:
    runtime = _runtime()
    relation = RelationRef(catalog="unit-project", namespace="raw", name="records")

    schema = runtime.schema_mapper.canonical_schema(
        [WriteField(name="id", data_type="STRING", mode="REQUIRED")]
    )
    fence = FencingToken(
        lease_table="unit-project.dander_meta._dander_leases",
        pipeline_id="records",
        run_id="run-123",
        token=7,
    )
    prepared = runtime.target_fence.prepare_dml("DELETE FROM `target` WHERE TRUE", fence)

    @dataclass
    class _Job:
        job_id: str = "job-123"
        output_rows: int = 11
        num_dml_affected_rows: int = 9
        total_bytes_processed: int = 1_024
        total_bytes_billed: int = 2_048

    telemetry = runtime.telemetry.operation(
        _Job(),
        operation=TelemetryOperation.LOAD,
        duration_ms=45,
        retry_count=1,
    )

    assert runtime.relation_codec.render(relation) == "`unit-project`.`raw`.`records`"
    assert schema.fields[0].data_type.kind is LogicalTypeKind.STRING
    assert "ASSERT @@row_count = 1" in prepared.sql
    assert isinstance(prepared.options, bigquery.QueryJobConfig)
    assert telemetry.to_payload() == {
        "provider": "bigquery",
        "operation": "load",
        "duration_ms": 45,
        "retry_count": 1,
        "rows_read": 0,
        "rows_written": 11,
        "rows_affected": 9,
        "bytes_read": 0,
        "bytes_written": 0,
        "bytes_processed": 1_024,
        "bytes_billed": 2_048,
        "job_id": "job-123",
    }


def test_bigquery_runtime_constructs_writers_through_capability(
    monkeypatch: MonkeyPatch,
) -> None:
    import dander.providers.bigquery.runtime as bigquery_runtime

    captured: list[tuple[str, dict[str, object]]] = []

    def replace(**kwargs: object) -> object:
        captured.append(("replace", kwargs))
        return object()

    def scd1(**kwargs: object) -> object:
        captured.append(("scd1", kwargs))
        return object()

    monkeypatch.setattr(bigquery_runtime, "BigQueryReplaceWriter", replace)
    monkeypatch.setattr(bigquery_runtime, "BigQueryScd1Writer", scd1)
    factory = _runtime().writers

    factory.build_ingestion_writer(
        sandbox=True,
        batch_rows=50,
        schema_evolution=SchemaEvolution.STRICT,
    )
    factory.build_ingestion_writer(
        sandbox=False,
        batch_rows=75,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert captured == [
        ("replace", {"project": "unit-project", "max_batch_rows": 50}),
        (
            "scd1",
            {
                "project": "unit-project",
                "max_batch_rows": 75,
                "schema_evolution": SchemaEvolution.ADDITIVE,
            },
        ),
    ]
