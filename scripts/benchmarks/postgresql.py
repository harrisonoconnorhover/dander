#!/usr/bin/env python3
"""Reproducible local PostgreSQL bounded-memory and concurrency benchmark."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.providers import ProviderKind, default_provider_registry
from dander.state import StateRuntime
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterator

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]
_REPORT_SCHEMA = "io.dander.benchmark.postgresql/v1"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Bounded local workload controls; the DSN is deliberately not part of the report."""

    rows: int = 100_000
    payload_bytes: int = 1_024
    batch_rows: int = 1_000
    concurrent_pipelines: int = 4
    concurrent_rows_per_pipeline: int = 5_000
    qualification_memory_limit_mib: int | None = None

    def __post_init__(self) -> None:
        limits = {
            "rows": 10_000_000,
            "payload_bytes": 1_048_576,
            "batch_rows": 100_000,
            "concurrent_pipelines": 32,
            "concurrent_rows_per_pipeline": 1_000_000,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            if value > maximum:
                raise ValueError(f"{name} must not exceed {maximum}")
        if self.batch_rows * self.payload_bytes > 256 * 1_024 * 1_024:
            raise ValueError("one configured batch must not exceed 256 MiB of payload")
        if self.qualification_memory_limit_mib is not None and (
            isinstance(self.qualification_memory_limit_mib, bool)
            or self.qualification_memory_limit_mib <= 0
        ):
            raise ValueError("qualification_memory_limit_mib must be a positive integer")


@dataclass(frozen=True, slots=True)
class PostgreSQLBenchmarkReport:
    """Non-sensitive measurements from one local PostgreSQL benchmark execution."""

    schema: str
    provider: str
    provider_version: str
    python_version: str
    rows: int
    logical_input_bytes: int
    payload_bytes: int
    batch_rows: int
    duration_seconds: float
    throughput_rows_per_second: float
    peak_rss_bytes: int
    concurrent_pipelines: int
    concurrent_rows: int
    concurrent_duration_seconds: float
    concurrent_throughput_rows_per_second: float
    stale_publication_rejected: bool
    temporary_staging_relations: int
    qualification_status: str
    qualification_reason: str
    cost_status: str = "not_measured"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def run_postgresql_benchmark(
    dsn: str,
    config: BenchmarkConfig,
) -> PostgreSQLBenchmarkReport:
    """Run bounded COPY, independent concurrency, and stale-fence probes."""
    if not dsn:
        raise ValueError("PostgreSQL benchmark requires a non-empty DSN")
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=max(5, config.concurrent_pipelines + 1),
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=10)
    suffix = uuid.uuid4().hex[:12]
    warehouse_schema = f"dander_benchmark_{suffix}"
    state_schema = f"dander_benchmark_state_{suffix}"
    with pool.connection() as connection:
        version_row = connection.execute(
            "SELECT current_database() AS database, current_setting('server_version') AS version"
        ).fetchone()
    if version_row is None:
        pool.close()
        raise RuntimeError("PostgreSQL benchmark could not read server metadata")
    database = cast("str", version_row["database"])
    provider_version = cast("str", version_row["version"])
    warehouse = _warehouse_runtime(pool, database)
    state = _state_runtime(pool, state_schema)
    state.migrator.migrate()

    try:
        peak_before = _peak_rss_bytes()
        started = time.perf_counter()
        bounded_rows = _write_bounded(
            warehouse,
            database=database,
            schema=warehouse_schema,
            rows=config.rows,
            payload_bytes=config.payload_bytes,
            batch_rows=config.batch_rows,
        )
        bounded_duration = max(time.perf_counter() - started, 1e-9)
        peak_after = _peak_rss_bytes()
        concurrent_rows, concurrent_duration = _write_concurrently(
            warehouse,
            state,
            database=database,
            schema=warehouse_schema,
            pipelines=config.concurrent_pipelines,
            rows_per_pipeline=config.concurrent_rows_per_pipeline,
            batch_rows=config.batch_rows,
        )
        stale_rejected = _reject_stale_publication(
            warehouse,
            state,
            database=database,
            schema=warehouse_schema,
        )
        staging_count = _temporary_staging_count(pool)
        logical_input = config.rows * (config.payload_bytes + 24)
        qualification_status, qualification_reason = _qualification(
            logical_input_bytes=logical_input,
            peak_rss_bytes=max(peak_before, peak_after),
            memory_limit_mib=config.qualification_memory_limit_mib,
        )
        return PostgreSQLBenchmarkReport(
            schema=_REPORT_SCHEMA,
            provider="postgresql",
            provider_version=provider_version,
            python_version=platform.python_version(),
            rows=bounded_rows,
            logical_input_bytes=logical_input,
            payload_bytes=config.payload_bytes,
            batch_rows=config.batch_rows,
            duration_seconds=round(bounded_duration, 6),
            throughput_rows_per_second=round(bounded_rows / bounded_duration, 3),
            peak_rss_bytes=max(peak_before, peak_after),
            concurrent_pipelines=config.concurrent_pipelines,
            concurrent_rows=concurrent_rows,
            concurrent_duration_seconds=round(concurrent_duration, 6),
            concurrent_throughput_rows_per_second=round(
                concurrent_rows / max(concurrent_duration, 1e-9), 3
            ),
            stale_publication_rejected=stale_rejected,
            temporary_staging_relations=staging_count,
            qualification_status=qualification_status,
            qualification_reason=qualification_reason,
        )
    finally:
        with pool.connection() as connection:
            for schema_name in (warehouse_schema, state_schema):
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
                )
        pool.close()


def _warehouse_runtime(pool: PostgreSQLPool, database: str) -> WarehouseRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {
            "provider": "postgresql",
            "database": database,
            "dsn_env": "DANDER_BENCHMARK_POSTGRES_DSN",
            "statement_timeout_ms": 300_000,
            "lock_timeout_ms": 30_000,
            "idle_transaction_timeout_ms": 60_000,
        },
    )
    runtime = registry.build(ProviderKind.WAREHOUSE, config, context={"pool": pool})
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("PostgreSQL benchmark received an invalid warehouse runtime")
    return runtime


def _state_runtime(pool: PostgreSQLPool, schema: str) -> StateRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": f"postgresql:{schema}",
            "dsn_env": "DANDER_BENCHMARK_POSTGRES_DSN",
            "schema_name": schema,
            "lease_seconds": 30,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": False},
    )
    if not isinstance(runtime, StateRuntime):
        raise TypeError("PostgreSQL benchmark received an invalid state runtime")
    return runtime


def _write_bounded(
    warehouse: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    rows: int,
    payload_bytes: int,
    batch_rows: int,
) -> int:
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=batch_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    relation = RelationRef(catalog=database, namespace=schema, name="bounded_records")
    fence = warehouse.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="benchmark_bounded",
            run_id="bounded-one",
            token=1,
            authority_id="postgresql:benchmark",
        ),
    )
    target = _target(database, schema, "bounded_records", fence)
    return _write_batches(
        writer,
        _records(rows, payload_bytes),
        target,
        batch_rows=batch_rows,
    )


def _write_concurrently(
    warehouse: WarehouseRuntime,
    state: StateRuntime,
    *,
    database: str,
    schema: str,
    pipelines: int,
    rows_per_pipeline: int,
    batch_rows: int,
) -> tuple[int, float]:
    def run(index: int) -> int:
        pipeline_id = f"benchmark_concurrent_{index}"
        run_id = f"concurrent-{index}"
        lease = state.leases.acquire(pipeline_id, run_id)
        if lease is None or lease.fence is None:
            raise RuntimeError("PostgreSQL benchmark could not acquire an independent lease")
        try:
            table = f"concurrent_{index}"
            relation = RelationRef(catalog=database, namespace=schema, name=table)
            publication = warehouse.target_fence.claim(relation, lease.fence)
            writer = warehouse.writers.build_ingestion_writer(
                sandbox=False,
                batch_rows=batch_rows,
                schema_evolution=SchemaEvolution.STRICT,
            )
            return _write_batches(
                writer,
                _records(rows_per_pipeline, 128, prefix=f"{index}-"),
                _target(database, schema, table, publication),
                batch_rows=batch_rows,
            )
        finally:
            state.leases.release(lease)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=pipelines) as executor:
        affected = sum(executor.map(run, range(pipelines)))
    return affected, max(time.perf_counter() - started, 1e-9)


def _reject_stale_publication(
    warehouse: WarehouseRuntime,
    state: StateRuntime,
    *,
    database: str,
    schema: str,
) -> bool:
    pipeline_id = "benchmark_stale"
    first = state.leases.acquire(pipeline_id, "stale-one")
    if first is None or first.fence is None:
        raise RuntimeError("PostgreSQL benchmark could not acquire its first stale probe lease")
    relation = RelationRef(catalog=database, namespace=schema, name="stale_records")
    stale_publication = warehouse.target_fence.claim(relation, first.fence)
    state.leases.release(first)
    second = state.leases.acquire(pipeline_id, "stale-two")
    if second is None or second.fence is None:
        raise RuntimeError("PostgreSQL benchmark could not acquire its second stale probe lease")
    current_publication = warehouse.target_fence.claim(relation, second.fence)
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.STRICT,
    )
    try:
        rejected = False
        try:
            writer.write(
                _records(1, 16),
                _target(database, schema, "stale_records", stale_publication),
            )
        except TargetFenceLostError:
            rejected = True
        if rejected:
            writer.write(
                _records(1, 16),
                _target(database, schema, "stale_records", current_publication),
            )
        return rejected
    finally:
        state.leases.release(second)


def _target(
    database: str,
    schema: str,
    table: str,
    publication_fence: TargetFence,
) -> WriteTarget:
    return WriteTarget(
        project=database,
        dataset=schema,
        table=table,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING"),
        ),
        publication_fence=publication_fence,
    )


def _write_batches(
    writer: WritePattern,
    records: Iterator[dict[str, object]],
    target: WriteTarget,
    *,
    batch_rows: int,
) -> int:
    affected = 0
    while batch := list(islice(records, batch_rows)):
        affected += writer.write(batch, target)
    return affected


def _records(rows: int, payload_bytes: int, *, prefix: str = "") -> Iterator[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{prefix}{index:012d}", "payload": f"{index:012d}{padding}"}


def _temporary_staging_count(pool: PostgreSQLPool) -> int:
    with pool.connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM pg_catalog.pg_class "
            "WHERE relname LIKE 'dander_stage_%' AND relpersistence = 't'"
        ).fetchone()
    return int(cast("int", row["count"] if row is not None else 0))


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _qualification(
    *,
    logical_input_bytes: int,
    peak_rss_bytes: int,
    memory_limit_mib: int | None,
) -> tuple[str, str]:
    if memory_limit_mib is None:
        return "not_evaluated", "No controlled container memory limit was supplied."
    memory_limit_bytes = memory_limit_mib * 1_024 * 1_024
    if logical_input_bytes < memory_limit_bytes * 10:
        return "failed", "Logical input was less than ten times the container memory limit."
    if peak_rss_bytes > memory_limit_bytes * 0.8:
        return "failed", "Peak RSS exceeded eighty percent of the container memory limit."
    return "passed", "Input and peak RSS met the published bounded-memory objective."


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="DANDER_TEST_POSTGRES_DSN")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--payload-bytes", type=int, default=1_024)
    parser.add_argument("--batch-rows", type=int, default=1_000)
    parser.add_argument("--concurrent-pipelines", type=int, default=4)
    parser.add_argument("--concurrent-rows", type=int, default=5_000)
    parser.add_argument("--qualification-memory-limit-mib", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        raise SystemExit(f"PostgreSQL DSN is required in {arguments.dsn_env}")
    report = run_postgresql_benchmark(
        dsn,
        BenchmarkConfig(
            rows=arguments.rows,
            payload_bytes=arguments.payload_bytes,
            batch_rows=arguments.batch_rows,
            concurrent_pipelines=arguments.concurrent_pipelines,
            concurrent_rows_per_pipeline=arguments.concurrent_rows,
            qualification_memory_limit_mib=arguments.qualification_memory_limit_mib,
        ),
    )
    output = report.to_json() + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
