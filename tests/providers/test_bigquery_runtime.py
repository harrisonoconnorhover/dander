"""BigQuery's first provider-selected warehouse composition."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from google.cloud import bigquery

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.bigquery.config import BigQueryWarehouseConfig
from dander.providers.bigquery.fence import BigQueryTargetFence
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
    assert runtime.capabilities.transports == frozenset(
        {WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE}
    )


def test_bigquery_config_translates_legacy_cli_coordinates() -> None:
    relation = BigQueryWarehouseConfig(provider="bigquery", dataset="profile_raw").raw_relation(
        "accounts",
        compatibility_catalog="unit-project",
        compatibility_namespace="landing",
    )

    assert relation == RelationRef(
        catalog="unit-project",
        namespace="landing",
        name="accounts",
    )


def test_bigquery_config_uses_its_native_namespace_when_cli_alias_is_absent() -> None:
    relation = BigQueryWarehouseConfig(provider="bigquery", dataset="profile_raw").raw_relation(
        "accounts",
        compatibility_catalog="unit-project",
        compatibility_namespace=None,
    )

    assert relation.namespace == "profile_raw"


def test_bigquery_runtime_exposes_codec_schema_fence_and_telemetry() -> None:
    runtime = _runtime()
    relation = RelationRef(catalog="unit-project", namespace="raw", name="records")

    schema = runtime.schema_mapper.canonical_schema(
        [WriteField(name="id", data_type="STRING", mode="REQUIRED")]
    )
    fence = TargetFence(
        fence_table="unit-project.raw._dander_target_commits",
        target_id="unit-project.raw.records",
        authority_id="bigquery:unit-project:dander_meta",
        authority_epoch=1,
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
    assert runtime.ingestion_schema_mapper is None
    assert schema.fields[0].data_type.kind is LogicalTypeKind.STRING
    assert prepared.sql.count("ASSERT @@row_count = 1") == 2
    assert "status = 'committed'" in prepared.sql
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
        "queue_duration_ms": 0,
        "execution_duration_ms": 0,
        "spill_bytes": 0,
        "job_id": "job-123",
    }


def test_bigquery_keeps_provider_native_types_out_of_portable_ingestion_preflight() -> None:
    runtime = _runtime()

    assert runtime.ingestion_schema_mapper is None
    with pytest.raises(ValueError, match="explicit canonical fallback"):
        runtime.schema_mapper.canonical_schema([WriteField(name="location", data_type="GEOGRAPHY")])


class _FenceJob:
    def __init__(self, *, affected: int | None = None) -> None:
        self.num_dml_affected_rows = affected

    def result(self) -> list[object]:
        return []


class _FenceClient:
    def __init__(self, *, claim_affected: int = 1) -> None:
        self.claim_affected = claim_affected
        self.queries: list[tuple[str, bigquery.QueryJobConfig | None]] = []

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FenceJob:
        self.queries.append((query, job_config))
        return _FenceJob(affected=self.claim_affected if query.startswith("MERGE") else None)


def test_bigquery_target_fence_claims_before_preparing_publication() -> None:
    client = _FenceClient()
    capability = BigQueryTargetFence(project="unit-project", client=client)
    relation = RelationRef(catalog="unit-project", namespace="raw", name="records")
    lease = FencingToken(
        lease_table=None,
        pipeline_id="records",
        run_id="run-123",
        token=7,
        authority_id="postgresql:state-primary",
        authority_epoch=3,
    )

    claim = capability.claim(relation, lease)
    prepared = capability.prepare_dml("MERGE `target` USING `stage` ON FALSE", claim)

    assert client.queries[0][0].startswith(
        "CREATE TABLE IF NOT EXISTS `unit-project.raw._dander_target_commits`"
    )
    claim_sql, claim_config = client.queries[1]
    assert claim_sql.startswith("MERGE `unit-project.raw._dander_target_commits`")
    assert "incoming.fencing_token > current.fencing_token" in claim_sql
    assert "incoming.run_id = current.run_id" in claim_sql
    assert claim_config is not None
    parameters = {parameter.name: parameter.value for parameter in claim_config.query_parameters}
    assert parameters == {
        "dander_target_id": "unit-project.raw.records",
        "dander_pipeline_id": "records",
        "dander_authority_id": "postgresql:state-primary",
        "dander_authority_epoch": 3,
        "dander_run_id": "run-123",
        "dander_fencing_token": 7,
    }
    assert prepared.sql.startswith("BEGIN TRANSACTION;\nUPDATE")
    assert "MERGE `target` USING `stage` ON FALSE;" in prepared.sql
    assert prepared.sql.endswith("COMMIT TRANSACTION;")


def test_bigquery_target_fence_rejects_zero_row_claim() -> None:
    capability = BigQueryTargetFence(
        project="unit-project",
        client=_FenceClient(claim_affected=0),
    )
    relation = RelationRef(catalog="unit-project", namespace="raw", name="records")
    lease = FencingToken(
        lease_table=None,
        pipeline_id="records",
        run_id="stale-run",
        token=2,
        authority_id="postgresql:state-primary",
    )

    with pytest.raises(TargetFenceLostError, match="rejected stale"):
        capability.claim(relation, lease)


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
    with pytest.raises(ValueError, match="does not select scd2"):
        factory.build_ingestion_writer(
            sandbox=False,
            batch_rows=80,
            schema_evolution=SchemaEvolution.STRICT,
            mode=WriteMode.SCD2,
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
