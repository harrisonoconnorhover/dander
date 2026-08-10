"""Small Snowflake connector protocols and sanitized session helpers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


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


@dataclass(frozen=True, slots=True)
class SnowflakeStatementResult:
    """Non-sensitive statement result retained after its cursor is closed."""

    rowcount: int
    query_id: str | None
    row: object | None = None
    rows: tuple[object, ...] = ()


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
    "execute",
    "execute_many",
    "open_connection",
]
