"""Small DB-API boundary for injected and SDK Redshift sessions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


class RedshiftCursor(Protocol):
    rowcount: int

    def execute(self, operation: str, args: Sequence[object] | None = None) -> object: ...
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


__all__ = [
    "RedshiftConnection",
    "RedshiftConnectionFactory",
    "RedshiftStatementResult",
    "execute",
    "open_connection",
]
