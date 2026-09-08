"""Leader-elected PostgreSQL producer for canonical Control schedule wakeups."""

from __future__ import annotations

import logging
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from dander.control.application import ControlOperationDependencyError
from dander.control.orchestration import TriggerKind, TriggerSpec
from dander.control.schedule_consumer import ControlScheduleConsumer, ScheduleQueueError
from dander.control.schedule_expression import (
    ScheduleExpressionError,
    floor_utc_minute,
    parse_five_field_cron,
    require_iana_time_zone,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from psycopg import Connection

    from dander.control.postgresql_control_database import (
        PostgreSQLControlDatabase,
        PostgreSQLRow,
    )
    from dander.control.postgresql_schedule_queue import PostgreSQLScheduleQueue

_LOGGER = logging.getLogger("dander.control.postgresql_scheduler")


class PostgreSQLSchedulerLeader:
    """Hold one session-level advisory lock on a dedicated pooled connection."""

    def __init__(self, database: PostgreSQLControlDatabase) -> None:
        self._database = database
        self._connection: Connection[PostgreSQLRow] | None = None
        self._closed = False

    def try_acquire(self) -> bool:
        """Acquire leadership without blocking, retaining the successful DB session."""
        if self._closed:
            raise ScheduleQueueError("The PostgreSQL scheduler leader is closed.")
        if self._connection is not None:
            try:
                with self._connection.transaction():
                    self._connection.execute("SELECT 1")
                return True
            except Exception:  # noqa: BLE001 - a dead session no longer owns its lock
                self._discard_connection()
        connection: Connection[PostgreSQLRow] | None = None
        try:
            connection = self._database.pool.getconn(timeout=2.0)
            with connection.transaction():
                row = connection.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                    (f"dander-control-scheduler:{self._database.schema_name}",),
                ).fetchone()
            acquired = row is not None and row.get("acquired") is True
            if acquired:
                self._connection = connection
                return True
            self._database.pool.putconn(connection)
            return False
        except Exception as error:
            if connection is not None:
                with suppress(Exception):
                    self._database.pool.putconn(connection)
            raise ScheduleQueueError("PostgreSQL scheduler leader election failed.") from error

    def close(self) -> None:
        """Release the exact session lock and return its dedicated connection."""
        if self._closed:
            return
        self._closed = True
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            with connection.transaction():
                row = connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s)) AS released",
                    (f"dander-control-scheduler:{self._database.schema_name}",),
                ).fetchone()
            if row is None or row.get("released") is not True:
                raise ScheduleQueueError("The PostgreSQL scheduler leader lock was not held.")
        except ScheduleQueueError:
            connection.close()
            raise
        except Exception as error:
            connection.close()
            raise ScheduleQueueError("PostgreSQL scheduler leader release failed.") from error
        finally:
            self._database.pool.putconn(connection)

    def require_connection(self) -> Connection[PostgreSQLRow]:
        """Return the live session that owns leadership for fenced schedule writes."""
        if self._closed or self._connection is None:
            raise ScheduleQueueError("The PostgreSQL scheduler is not the leader.")
        return self._connection

    def _discard_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        finally:
            self._database.pool.putconn(connection)


class PostgreSQLScheduler:
    """One bounded producer thread; only the advisory-lock holder advances cursors."""

    def __init__(
        self,
        database: PostgreSQLControlDatabase,
        queue: PostgreSQLScheduleQueue,
        triggers: Iterable[TriggerSpec],
        *,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 15.0,
        shutdown_grace_seconds: float = 35.0,
        initial_cursor: datetime | None = None,
        max_scan_minutes: int = 10_080,
        max_occurrences: int = 1_000,
    ) -> None:
        if poll_interval_seconds <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("PostgreSQL scheduler timing must be positive.")
        if isinstance(max_scan_minutes, bool) or not 1 <= max_scan_minutes <= 525_600:
            raise ValueError("PostgreSQL scheduler scan minutes must be 1 to 525600.")
        if isinstance(max_occurrences, bool) or not 1 <= max_occurrences <= 10_000:
            raise ValueError("PostgreSQL scheduler occurrence bound must be 1 to 10000.")
        selected: dict[str, TriggerSpec] = {}
        for spec in triggers:
            if (
                spec.kind is not TriggerKind.SCHEDULE
                or spec.schedule is None
                or spec.time_zone is None
            ):
                raise ValueError("The PostgreSQL scheduler accepts scheduled triggers only.")
            try:
                parse_five_field_cron(spec.schedule)
                require_iana_time_zone(spec.time_zone)
            except ScheduleExpressionError as error:
                raise ValueError("The PostgreSQL schedule definition is invalid.") from error
            existing = selected.setdefault(spec.trigger_id, spec)
            if existing != spec:
                raise ValueError("The PostgreSQL schedule registry has conflicting trigger ids.")
        if not selected or len(selected) > 100:
            raise ValueError("The PostgreSQL scheduler requires 1 to 100 unique triggers.")
        self._queue = queue
        self._triggers = tuple(selected.values())
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_interval = float(poll_interval_seconds)
        self._shutdown_grace = float(shutdown_grace_seconds)
        self._initial_cursor = (
            floor_utc_minute(initial_cursor) if initial_cursor is not None else None
        )
        self._max_scan_minutes = max_scan_minutes
        self._max_occurrences = max_occurrences
        self._leader = PostgreSQLSchedulerLeader(database)
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._initial_tick_complete = False
        self._last_tick_failed = False
        self._closed = False
        self._cleanup_complete = False
        self._close_lock = threading.Lock()

    def start(self) -> None:
        """Start the scheduler producer thread idempotently."""
        with self._state_lock:
            if self._closed:
                raise ControlOperationDependencyError("The PostgreSQL scheduler is closed.")
            if self._thread is not None:
                return
            thread = threading.Thread(
                target=self._loop,
                name="dander-control-postgresql-scheduler",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def ready(self) -> bool:
        """Report healthy after one successful leader check, including on followers."""
        with self._state_lock:
            thread = self._thread
            return (
                not self._closed
                and thread is not None
                and thread.is_alive()
                and self._initial_tick_complete
                and not self._last_tick_failed
            )

    def tick_once(self) -> int:
        """Produce due wakeups once when this process owns the dedicated session lock."""
        if self._stop.is_set():
            return 0
        try:
            if not self._leader.try_acquire():
                produced = 0
            else:
                horizon = floor_utc_minute(self._clock())
                if self._initial_cursor is None:
                    self._initial_cursor = horizon - timedelta(minutes=1)
                produced = self._queue.enqueue_due(
                    self._triggers,
                    through=horizon,
                    initial_cursor=self._initial_cursor,
                    max_scan_minutes=self._max_scan_minutes,
                    max_occurrences=self._max_occurrences,
                    connection=self._leader.require_connection(),
                )
            with self._state_lock:
                self._initial_tick_complete = True
                self._last_tick_failed = False
            return produced
        except (ScheduleExpressionError, ScheduleQueueError):
            with self._state_lock:
                self._last_tick_failed = True
            raise

    def request_stop(self) -> None:
        """Stop accepting work without closing dependencies used by an active worker."""
        with self._state_lock:
            self._closed = True
        self._stop.set()

    def close(self) -> None:
        """Wait for the worker before cleanup, retaining ownership after a timeout."""
        with self._close_lock:
            if self._cleanup_complete:
                return
            self.request_stop()
            with self._state_lock:
                thread = self._thread
            if thread is not None:
                thread.join(timeout=self._shutdown_grace)
                if thread.is_alive():
                    raise ControlOperationDependencyError(
                        "The PostgreSQL scheduler did not stop within its shutdown grace period."
                    )
            try:
                self._leader.close()
            except ScheduleQueueError as error:
                raise ControlOperationDependencyError(
                    "The PostgreSQL scheduler could not release leadership."
                ) from error
            self._cleanup_complete = True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except (ScheduleExpressionError, ScheduleQueueError):
                _LOGGER.warning("control_postgresql_schedule_tick_failed")
            self._stop.wait(self._poll_interval)


class PostgreSQLScheduleSubmissionSource:
    """Own one producer and the existing canonical Control schedule consumer."""

    def __init__(
        self,
        producer: PostgreSQLScheduler,
        consumer: ControlScheduleConsumer,
    ) -> None:
        self._producer = producer
        self._consumer = consumer
        self._closed = False
        self._cleanup_complete = False
        self._close_lock = threading.Lock()

    def start(self) -> None:
        """Start consumption before enabling occurrence production."""
        if self._closed:
            raise ControlOperationDependencyError(
                "The PostgreSQL schedule submission source is closed."
            )
        self._consumer.start()
        self._producer.start()

    def ready(self) -> bool:
        """Require both the producer and consumer loops to be healthy."""
        return not self._closed and self._producer.ready() and self._consumer.ready()

    def close(self) -> None:
        """Stop both loops; retain the shared queue until the producer has stopped."""
        with self._close_lock:
            if self._cleanup_complete:
                return
            self._closed = True
            self._producer.request_stop()
            self._consumer.request_stop()
            self._producer.close()
            self._consumer.close()
            self._cleanup_complete = True


__all__ = [
    "PostgreSQLScheduleSubmissionSource",
    "PostgreSQLScheduler",
    "PostgreSQLSchedulerLeader",
]
