"""Small DB-API boundary for injected and SDK Redshift sessions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from dander.telemetry import OperationTelemetry

_QUERY_HISTORY_ID_LIMIT = 1_000
_REDSHIFT_BLOCK_BYTES = 1_024 * 1_024


class RedshiftCursor(Protocol):
    rowcount: int

    def execute(self, operation: str, args: Sequence[object] | None = None) -> object: ...
    def executemany(self, operation: str, args: Iterable[Sequence[object]]) -> object: ...
    def fetchone(self) -> object | None: ...
    def fetchall(self) -> list[object]: ...
    def close(self) -> None: ...


class RedshiftConnection(Protocol):
    autocommit: bool

    def cursor(self) -> RedshiftCursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class RedshiftConnectionFactory(Protocol):
    def __call__(self) -> RedshiftConnection: ...


@dataclass(frozen=True, slots=True)
class RedshiftStatementResult:
    rowcount: int
    row: object | None = None
    rows: tuple[object, ...] = ()
    query_id: str | None = None


@contextmanager
def open_connection(factory: RedshiftConnectionFactory) -> Iterator[RedshiftConnection]:
    connection = factory()
    connection.autocommit = False
    try:
        yield connection
    finally:
        connection.close()


def execute(
    connection: RedshiftConnection,
    statement: str,
    parameters: Sequence[object] = (),
    *,
    fetch: Literal["one", "all"] | None = None,
) -> RedshiftStatementResult:
    cursor = connection.cursor()
    try:
        cursor.execute(statement, parameters or None)
        row = cursor.fetchone() if fetch == "one" else None
        rows = tuple(cursor.fetchall()) if fetch == "all" else ()
        return RedshiftStatementResult(rowcount=cursor.rowcount, row=row, rows=rows)
    finally:
        cursor.close()


def execute_many(
    connection: RedshiftConnection,
    statement: str,
    parameters: Iterable[Sequence[object]],
) -> RedshiftStatementResult:
    """Execute one bounded parameter batch without retaining provider response data."""
    cursor = connection.cursor()
    try:
        cursor.executemany(statement, parameters)
        return RedshiftStatementResult(rowcount=cursor.rowcount)
    finally:
        cursor.close()


def capture_last_query_id(connection: RedshiftConnection) -> str | None:
    """Capture a completed compute query ID after its transaction is already committed."""
    try:
        row = execute(connection, "SELECT last_user_query_id()", fetch="one").row
        value = row[0] if isinstance(row, (tuple, list)) and row else None
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return str(value) if valid else None
    except Exception:
        return None
    finally:
        # The lookup itself is telemetry-only. End it even when a denied lookup aborted the
        # transaction so later correctness or cleanup SQL starts from a usable session.
        _rollback_telemetry_transaction(connection)


def enrich_operation_telemetry(
    connection: RedshiftConnection,
    operations: Sequence[OperationTelemetry],
) -> tuple[OperationTelemetry, ...]:
    """Best-effort enrichment for already captured COPY or CTAS query IDs."""
    requested = _recent_query_ids(operations)
    if not requested:
        return tuple(operations)
    placeholders = ", ".join("%s" for _query_id in requested)
    statement = (
        "WITH recent AS (SELECT query_id, queue_time, execution_time, "
        "TRIM(service_class_name) AS service_class_name, "
        "TRIM(compute_type) AS compute_type FROM sys_query_history "
        f"WHERE status = 'success' AND query_id IN ({placeholders})), "
        "detail AS (SELECT query_id, SUM(input_bytes) AS input_bytes, "
        "SUM(spilled_block_local_disk) AS local_spill_blocks, "
        "SUM(spilled_block_remote_disk) AS remote_spill_blocks FROM sys_query_detail "
        "WHERE metrics_level = 'Step' AND query_id IN (SELECT query_id FROM recent) "
        "GROUP BY query_id), loads AS (SELECT query_id, loaded_rows, loaded_bytes, "
        "source_file_bytes, copy_job_id FROM sys_load_history "
        "WHERE status = 'completed' AND query_id IN (SELECT query_id FROM recent)) "
        "SELECT recent.query_id, recent.queue_time, recent.execution_time, "
        "recent.service_class_name, recent.compute_type, detail.input_bytes, "
        "detail.local_spill_blocks, detail.remote_spill_blocks, "
        "loads.loaded_rows, loads.loaded_bytes, loads.source_file_bytes, loads.copy_job_id "
        "FROM recent LEFT JOIN detail USING (query_id) LEFT JOIN loads USING (query_id)"
    )
    try:
        rows = execute(connection, statement, requested, fetch="all").rows
        metrics = {
            parsed.query_id: parsed for row in rows if (parsed := _query_metrics(row)) is not None
        }
        return tuple(_enrich_operation(operation, metrics) for operation in operations)
    except Exception:
        return tuple(operations)
    finally:
        # A read failure aborts Redshift's current transaction. Always clear that telemetry-only
        # transaction before the caller performs temporary-table cleanup.
        _rollback_telemetry_transaction(connection)


@dataclass(frozen=True, slots=True)
class _RedshiftQueryMetrics:
    query_id: str
    queue_duration_ms: int
    execution_duration_ms: int
    resource_name: str | None
    resource_size: str | None
    bytes_processed: int
    spill_bytes: int
    loaded_rows: int
    loaded_bytes: int
    source_file_bytes: int
    copy_job_id: str | None


def _recent_query_ids(operations: Sequence[OperationTelemetry]) -> tuple[int, ...]:
    requested: list[int] = []
    seen: set[int] = set()
    for operation in reversed(operations):
        raw = operation.query_id
        if raw is None:
            continue
        try:
            query_id = int(raw)
        except (TypeError, ValueError):
            continue
        if query_id < 0 or query_id in seen:
            continue
        seen.add(query_id)
        requested.append(query_id)
        if len(requested) == _QUERY_HISTORY_ID_LIMIT:
            break
    requested.reverse()
    return tuple(requested)


def _query_metrics(row: object) -> _RedshiftQueryMetrics | None:
    if not isinstance(row, (tuple, list)) or len(row) != 12:
        return None
    query_id = _nonnegative_integer(row[0])
    queue_us = _nonnegative_integer(row[1])
    execution_us = _nonnegative_integer(row[2])
    if query_id is None or queue_us is None or execution_us is None:
        return None
    input_bytes = _optional_counter(row[5])
    local_spill = _optional_counter(row[6])
    remote_spill = _optional_counter(row[7])
    copy_job = _optional_counter(row[11])
    return _RedshiftQueryMetrics(
        query_id=str(query_id),
        queue_duration_ms=_microseconds_to_milliseconds(queue_us),
        execution_duration_ms=_microseconds_to_milliseconds(execution_us),
        resource_name=_optional_text(row[3]),
        resource_size=_optional_text(row[4]),
        bytes_processed=input_bytes,
        spill_bytes=(local_spill + remote_spill) * _REDSHIFT_BLOCK_BYTES,
        loaded_rows=_optional_counter(row[8]),
        loaded_bytes=_optional_counter(row[9]),
        source_file_bytes=_optional_counter(row[10]),
        copy_job_id=str(copy_job) if copy_job > 0 else None,
    )


def _enrich_operation(
    operation: OperationTelemetry,
    metrics: dict[str, _RedshiftQueryMetrics],
) -> OperationTelemetry:
    if operation.query_id is None or (query := metrics.get(operation.query_id)) is None:
        return operation
    return replace(
        operation,
        rows_written=query.loaded_rows or operation.rows_written,
        bytes_read=query.source_file_bytes or operation.bytes_read,
        bytes_written=query.loaded_bytes or operation.bytes_written,
        bytes_processed=query.bytes_processed,
        job_id=query.copy_job_id or operation.job_id,
        queue_duration_ms=query.queue_duration_ms,
        execution_duration_ms=query.execution_duration_ms,
        spill_bytes=query.spill_bytes,
        resource_name=query.resource_name or operation.resource_name,
        resource_size=query.resource_size or operation.resource_size,
    )


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _optional_counter(value: object) -> int:
    return _nonnegative_integer(value) or 0


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _microseconds_to_milliseconds(value: int) -> int:
    return (value + 999) // 1_000


def _rollback_telemetry_transaction(connection: RedshiftConnection) -> None:
    try:
        connection.rollback()
    except Exception:
        # Telemetry is best-effort and must never obscure a completed warehouse operation.
        return


__all__ = [
    "RedshiftConnection",
    "RedshiftConnectionFactory",
    "RedshiftStatementResult",
    "capture_last_query_id",
    "enrich_operation_telemetry",
    "execute",
    "open_connection",
]
