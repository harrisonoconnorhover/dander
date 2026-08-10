"""Small Snowflake connector protocols and sanitized session helpers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from dander.telemetry import OperationTelemetry


class SnowflakeCursor(Protocol):
    """Connector cursor surface used by Dander and its stateful test double."""

    rowcount: int
    sfqid: str | None

    def execute(self, command: str, params: Sequence[object] | None = None) -> Self: ...

    def executemany(
        self,
        command: str,
        seq_of_parameters: Sequence[Sequence[object]],
    ) -> Self: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> list[object]: ...

    def close(self) -> None: ...


class SnowflakeConnection(Protocol):
    """Connector connection surface kept independent from the optional SDK import."""

    def cursor(self) -> SnowflakeCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


SnowflakeConnectionFactory = Callable[[], SnowflakeConnection]

_QUERY_HISTORY_RESULT_LIMIT = 10_000
_QUERY_HISTORY_ID_LIMIT = 1_000


@dataclass(frozen=True, slots=True)
class SnowflakeStatementResult:
    """Non-sensitive statement result retained after its cursor is closed."""

    rowcount: int
    query_id: str | None
    row: object | None = None
    rows: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class _SnowflakeQueryMetrics:
    """Stable, non-sensitive counters available from same-session query history."""

    query_id: str
    resource_size: str | None
    bytes_processed: int
    execution_duration_ms: int
    queue_duration_ms: int
    rows_inserted: int


def execute(
    connection: SnowflakeConnection,
    statement: str,
    parameters: Sequence[object] = (),
    *,
    fetch: str = "none",
) -> SnowflakeStatementResult:
    """Execute one statement and detach only the bounded result requested by the caller."""
    cursor = connection.cursor()
    try:
        cursor.execute(statement, parameters or None)
        row = cursor.fetchone() if fetch == "one" else None
        rows = tuple(cursor.fetchall()) if fetch == "all" else ()
        return SnowflakeStatementResult(
            rowcount=max(cursor.rowcount, 0),
            query_id=cursor.sfqid,
            row=row,
            rows=rows,
        )
    finally:
        cursor.close()


def execute_many(
    connection: SnowflakeConnection,
    statement: str,
    parameter_rows: Sequence[Sequence[object]],
) -> SnowflakeStatementResult:
    """Execute one bounded qmark batch and detach its sanitized statement result."""
    cursor = connection.cursor()
    try:
        cursor.executemany(statement, parameter_rows)
        return SnowflakeStatementResult(
            rowcount=max(cursor.rowcount, 0),
            query_id=cursor.sfqid,
        )
    finally:
        cursor.close()


def enrich_operation_telemetry(
    connection: SnowflakeConnection,
    operations: Sequence[OperationTelemetry],
) -> tuple[OperationTelemetry, ...]:
    """Best-effort enrichment from bounded same-session Snowflake query history.

    Provider history is observability, not pipeline correctness. Missing, delayed, malformed,
    or unavailable history therefore leaves the already valid operation telemetry unchanged.
    """
    requested = _recent_query_ids(operations)
    if not requested:
        return tuple(operations)
    placeholders = ", ".join("?" for _query_id in requested)
    statement = (
        "SELECT QUERY_ID, WAREHOUSE_SIZE, BYTES_SCANNED, EXECUTION_TIME, "
        "QUEUED_PROVISIONING_TIME, QUEUED_REPAIR_TIME, QUEUED_OVERLOAD_TIME, "
        "ROWS_INSERTED FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION("
        f"RESULT_LIMIT => {_QUERY_HISTORY_RESULT_LIMIT}, "
        "INCLUDE_CLIENT_GENERATED_STATEMENT => TRUE)) "
        f"WHERE QUERY_ID IN ({placeholders})"
    )
    try:
        rows = execute(connection, statement, requested, fetch="all").rows
        metrics = {
            parsed.query_id: parsed for row in rows if (parsed := _query_metrics(row)) is not None
        }
        return tuple(_enrich_operation(operation, metrics) for operation in operations)
    except Exception:
        return tuple(operations)


def _recent_query_ids(operations: Sequence[OperationTelemetry]) -> tuple[str, ...]:
    requested: list[str] = []
    seen: set[str] = set()
    for operation in reversed(operations):
        query_id = operation.query_id
        if query_id is None or query_id in seen:
            continue
        seen.add(query_id)
        requested.append(query_id)
        if len(requested) == _QUERY_HISTORY_ID_LIMIT:
            break
    requested.reverse()
    return tuple(requested)


def _query_metrics(row: object) -> _SnowflakeQueryMetrics | None:
    if not isinstance(row, (tuple, list)) or len(row) != 8:
        return None
    query_id, resource_size, *numeric = row
    if not isinstance(query_id, str) or not query_id:
        return None
    values = tuple(_nonnegative_integer(value) for value in numeric)
    if any(value is None for value in values):
        return None
    bytes_processed, execution_ms, provisioning_ms, repair_ms, overload_ms, rows_inserted = values
    assert bytes_processed is not None
    assert execution_ms is not None
    assert provisioning_ms is not None
    assert repair_ms is not None
    assert overload_ms is not None
    assert rows_inserted is not None
    return _SnowflakeQueryMetrics(
        query_id=query_id,
        resource_size=resource_size if isinstance(resource_size, str) and resource_size else None,
        bytes_processed=bytes_processed,
        execution_duration_ms=execution_ms,
        queue_duration_ms=provisioning_ms + repair_ms + overload_ms,
        rows_inserted=rows_inserted,
    )


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _enrich_operation(
    operation: OperationTelemetry,
    metrics: dict[str, _SnowflakeQueryMetrics],
) -> OperationTelemetry:
    if operation.query_id is None or (query := metrics.get(operation.query_id)) is None:
        return operation
    return replace(
        operation,
        rows_written=operation.rows_written or query.rows_inserted,
        bytes_processed=query.bytes_processed,
        queue_duration_ms=query.queue_duration_ms,
        execution_duration_ms=query.execution_duration_ms,
        resource_size=query.resource_size or operation.resource_size,
    )


@contextmanager
def open_connection(factory: SnowflakeConnectionFactory) -> Iterator[SnowflakeConnection]:
    """Close one connector session after success or failure."""
    connection = factory()
    try:
        yield connection
    finally:
        connection.close()


__all__ = [
    "SnowflakeConnection",
    "SnowflakeConnectionFactory",
    "SnowflakeCursor",
    "SnowflakeStatementResult",
    "enrich_operation_telemetry",
    "execute",
    "execute_many",
    "open_connection",
]
