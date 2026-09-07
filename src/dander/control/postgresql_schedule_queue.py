"""PostgreSQL-backed schedule cursor and at-least-once wakeup queue."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from psycopg import sql

from dander.control.orchestration import ScheduleWakeup, TriggerKind, TriggerSpec
from dander.control.orchestration_serialization import (
    deserialize_schedule_wakeup,
    serialize_schedule_wakeup,
    serialize_trigger_spec,
)
from dander.control.schedule_consumer import (
    QueuedScheduleMessage,
    ScheduleQueueError,
)
from dander.control.schedule_expression import (
    ScheduleExpressionError,
    floor_utc_minute,
    iter_schedule_occurrences,
    parse_five_field_cron,
    require_iana_time_zone,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from zoneinfo import ZoneInfo

    from psycopg import Connection

    from dander.control.postgresql_control_database import (
        PostgreSQLControlDatabase,
        PostgreSQLRow,
    )
    from dander.control.schedule_expression import FiveFieldCron


class PostgreSQLScheduleQueue:
    """Durable cursor producer plus leased queue consumer over one Control schema."""

    def __init__(
        self,
        database: PostgreSQLControlDatabase,
        *,
        batch_size: int = 10,
        visibility_timeout_seconds: int = 60,
        max_deliveries: int = 8,
    ) -> None:
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 10:
            raise ValueError("The PostgreSQL schedule batch size must be 1 to 10.")
        if (
            isinstance(visibility_timeout_seconds, bool)
            or not 1 <= visibility_timeout_seconds <= 3_600
        ):
            raise ValueError(
                "The PostgreSQL schedule visibility timeout must be 1 to 3600 seconds."
            )
        if isinstance(max_deliveries, bool) or not 1 <= max_deliveries <= 100:
            raise ValueError("The PostgreSQL schedule delivery bound must be 1 to 100.")
        self._database = database
        self._batch_size = batch_size
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._max_deliveries = max_deliveries
        self._closed = False

    def enqueue_due(
        self,
        triggers: Iterable[TriggerSpec],
        *,
        through: datetime,
        initial_cursor: datetime,
        max_scan_minutes: int = 10_080,
        max_occurrences: int = 1_000,
        connection: Connection[PostgreSQLRow] | None = None,
    ) -> int:
        """Advance cursors and enqueue canonical occurrences in one fenced transaction.

        The leader-elected producer supplies the session that owns its advisory lock. Direct calls
        use one pooled connection for conformance and administrative catch-up.
        """
        self._require_open()
        horizon = floor_utc_minute(through)
        first_cursor = floor_utc_minute(initial_cursor)
        if first_cursor > horizon:
            raise ScheduleQueueError("The schedule initial cursor cannot follow its horizon.")
        specs = tuple(triggers)
        if len(specs) > 100:
            raise ScheduleQueueError("The PostgreSQL scheduler accepts at most 100 triggers.")
        prepared: list[tuple[TriggerSpec, FiveFieldCron, ZoneInfo, str]] = []
        seen: set[str] = set()
        for spec in specs:
            if spec.trigger_id in seen:
                raise ScheduleQueueError("The PostgreSQL schedule registry has duplicate triggers.")
            seen.add(spec.trigger_id)
            if (
                spec.kind is not TriggerKind.SCHEDULE
                or spec.schedule is None
                or spec.time_zone is None
            ):
                raise ScheduleQueueError(
                    "The PostgreSQL scheduler accepts scheduled triggers only."
                )
            try:
                expression = parse_five_field_cron(spec.schedule)
                zone = require_iana_time_zone(spec.time_zone)
            except ScheduleExpressionError as error:
                raise ScheduleQueueError(
                    "The PostgreSQL schedule definition is invalid."
                ) from error
            spec_sha256 = hashlib.sha256(serialize_trigger_spec(spec)).hexdigest()
            prepared.append((spec, expression, zone, spec_sha256))
        try:
            if connection is not None:
                return self._enqueue_prepared(
                    connection,
                    prepared,
                    horizon=horizon,
                    first_cursor=first_cursor,
                    max_scan_minutes=max_scan_minutes,
                    max_occurrences=max_occurrences,
                )
            with self._database.pool.connection() as pooled_connection:
                return self._enqueue_prepared(
                    pooled_connection,
                    prepared,
                    horizon=horizon,
                    first_cursor=first_cursor,
                    max_scan_minutes=max_scan_minutes,
                    max_occurrences=max_occurrences,
                )
        except ScheduleQueueError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize PostgreSQL failures
            raise ScheduleQueueError("The PostgreSQL schedule enqueue failed.") from error

    def _enqueue_prepared(
        self,
        connection: Connection[PostgreSQLRow],
        prepared: list[tuple[TriggerSpec, FiveFieldCron, ZoneInfo, str]],
        *,
        horizon: datetime,
        first_cursor: datetime,
        max_scan_minutes: int,
        max_occurrences: int,
    ) -> int:
        produced = 0
        with connection.transaction():
            for spec, expression, zone, spec_sha256 in prepared:
                row = connection.execute(
                    sql.SQL(
                        "SELECT spec_sha256, cursor_utc FROM {} WHERE trigger_id = %s FOR UPDATE"
                    ).format(self._database.relation("dander_schedule_cursors")),
                    (spec.trigger_id,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        sql.SQL(
                            "INSERT INTO {} "
                            "(trigger_id, spec_sha256, cursor_utc, updated_at) "
                            "VALUES (%s, %s, %s, clock_timestamp())"
                        ).format(self._database.relation("dander_schedule_cursors")),
                        (spec.trigger_id, spec_sha256, first_cursor),
                    )
                    cursor = first_cursor
                else:
                    stored_spec_sha256 = row.get("spec_sha256")
                    if not isinstance(stored_spec_sha256, str):
                        raise ScheduleQueueError(
                            "The PostgreSQL schedule specification identity is corrupt."
                        )
                    cursor_value = row.get("cursor_utc")
                    if not isinstance(cursor_value, datetime):
                        raise ScheduleQueueError("The PostgreSQL schedule cursor is corrupt.")
                    cursor = floor_utc_minute(cursor_value)
                    if stored_spec_sha256 != spec_sha256:
                        # A changed cron, time zone, enabled state, or plan revision must not be
                        # projected backward across the old definition's durable cursor.  Cut the
                        # new definition over at this tick's horizon; its first eligible minute is
                        # evaluated by the next tick.
                        connection.execute(
                            sql.SQL(
                                "UPDATE {} SET spec_sha256 = %s, cursor_utc = %s, "
                                "updated_at = clock_timestamp() WHERE trigger_id = %s"
                            ).format(self._database.relation("dander_schedule_cursors")),
                            (spec_sha256, horizon, spec.trigger_id),
                        )
                        continue
                if cursor > horizon:
                    raise ScheduleQueueError("The PostgreSQL schedule cursor is ahead of time.")
                # A UTC minute can yield at most one occurrence.  Limit each committed catch-up
                # page by both the scan and occurrence bounds so an every-minute schedule cannot
                # repeatedly overflow and roll back the same stale cursor after downtime.
                page_minutes = (
                    max_scan_minutes if not spec.enabled else min(max_scan_minutes, max_occurrences)
                )
                scan_horizon = min(horizon, cursor + timedelta(minutes=page_minutes))
                occurrences = (
                    iter_schedule_occurrences(
                        expression,
                        zone,
                        after=cursor,
                        through=scan_horizon,
                        max_minutes=max_scan_minutes,
                        max_occurrences=max_occurrences,
                    )
                    if spec.enabled
                    else ()
                )
                for occurrence in occurrences:
                    body = serialize_schedule_wakeup(
                        ScheduleWakeup(
                            trigger_id=spec.trigger_id,
                            plan_revision=spec.plan_revision,
                            scheduled_occurrence=occurrence,
                        )
                    )
                    message_id = hashlib.sha256(body).hexdigest()
                    inserted = connection.execute(
                        sql.SQL(
                            "INSERT INTO {} "
                            "(message_id, trigger_id, plan_revision, scheduled_occurrence, "
                            "body, available_at, delivery_count, receipt_handle, lease_until, "
                            "dead_lettered_at, created_at) "
                            "VALUES (%s, %s, %s, %s, %s, clock_timestamp(), 0, NULL, NULL, "
                            "NULL, clock_timestamp()) "
                            "ON CONFLICT (trigger_id, plan_revision, scheduled_occurrence) "
                            "DO NOTHING RETURNING message_id"
                        ).format(self._database.relation("dander_schedule_queue")),
                        (
                            message_id,
                            spec.trigger_id,
                            spec.plan_revision,
                            occurrence,
                            body,
                        ),
                    ).fetchone()
                    produced += int(inserted is not None)
                connection.execute(
                    sql.SQL(
                        "UPDATE {} SET spec_sha256 = %s, cursor_utc = %s, "
                        "updated_at = clock_timestamp() WHERE trigger_id = %s"
                    ).format(self._database.relation("dander_schedule_cursors")),
                    (spec_sha256, scan_horizon, spec.trigger_id),
                )
        return produced

    def receive(self) -> tuple[QueuedScheduleMessage, ...]:
        """Lease one bounded visible batch with fresh receipts and ``SKIP LOCKED``."""
        self._require_open()
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                relation = self._database.relation("dander_schedule_queue")
                connection.execute(
                    sql.SQL(
                        "UPDATE {} SET dead_lettered_at = clock_timestamp(), "
                        "receipt_handle = NULL, lease_until = NULL "
                        "WHERE dead_lettered_at IS NULL AND delivery_count >= %s "
                        "AND (lease_until IS NULL OR lease_until <= clock_timestamp())"
                    ).format(relation),
                    (self._max_deliveries,),
                )
                rows = connection.execute(
                    sql.SQL(
                        "SELECT message_id, body FROM {} "
                        "WHERE dead_lettered_at IS NULL AND available_at <= clock_timestamp() "
                        "AND delivery_count < %s "
                        "AND (lease_until IS NULL OR lease_until <= clock_timestamp()) "
                        "ORDER BY available_at, created_at, message_id "
                        "FOR UPDATE SKIP LOCKED LIMIT %s"
                    ).format(relation),
                    (self._max_deliveries, self._batch_size),
                ).fetchall()
                messages: list[QueuedScheduleMessage] = []
                for row in rows:
                    message_id = row.get("message_id")
                    raw_body = row.get("body")
                    body = (
                        bytes(raw_body)
                        if isinstance(raw_body, (bytes, bytearray, memoryview))
                        else b""
                    )
                    try:
                        canonical = serialize_schedule_wakeup(deserialize_schedule_wakeup(body))
                    except Exception:  # noqa: BLE001 - malformed durable bytes are quarantined
                        canonical = b""
                    if (
                        not isinstance(message_id, str)
                        or hashlib.sha256(body).hexdigest() != message_id
                        or canonical != body
                    ):
                        if isinstance(message_id, str):
                            connection.execute(
                                sql.SQL(
                                    "UPDATE {} SET dead_lettered_at = clock_timestamp(), "
                                    "receipt_handle = NULL, lease_until = NULL "
                                    "WHERE message_id = %s"
                                ).format(relation),
                                (message_id,),
                            )
                        continue
                    receipt = str(uuid.uuid4())
                    updated = connection.execute(
                        sql.SQL(
                            "UPDATE {} SET delivery_count = delivery_count + 1, "
                            "receipt_handle = %s, "
                            "lease_until = clock_timestamp() + (%s * INTERVAL '1 second') "
                            "WHERE message_id = %s RETURNING message_id"
                        ).format(relation),
                        (receipt, self._visibility_timeout_seconds, message_id),
                    ).fetchone()
                    if updated is None:
                        raise ScheduleQueueError("The PostgreSQL schedule lease was lost.")
                    messages.append(QueuedScheduleMessage(receipt_handle=receipt, body=body))
                return tuple(messages)
        except ScheduleQueueError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize PostgreSQL failures
            raise ScheduleQueueError("The PostgreSQL schedule receive failed.") from error

    def delete(self, receipt_handle: str) -> None:
        """Acknowledge only the exact current lease receipt."""
        self._require_open()
        try:
            parsed = uuid.UUID(receipt_handle)
        except (AttributeError, TypeError, ValueError) as error:
            raise ScheduleQueueError("Schedule queue receipt is invalid.") from error
        if str(parsed) != receipt_handle:
            raise ScheduleQueueError("Schedule queue receipt is invalid.")
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                deleted = connection.execute(
                    sql.SQL(
                        "DELETE FROM {} WHERE receipt_handle = %s "
                        "AND dead_lettered_at IS NULL AND lease_until > clock_timestamp() "
                        "RETURNING message_id"
                    ).format(self._database.relation("dander_schedule_queue")),
                    (receipt_handle,),
                ).fetchone()
                if deleted is None:
                    raise ScheduleQueueError("The PostgreSQL schedule receipt is stale.")
        except ScheduleQueueError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize PostgreSQL failures
            raise ScheduleQueueError("The PostgreSQL schedule delete failed.") from error

    def close(self) -> None:
        """Close this adapter without closing the shared PostgreSQL pool."""
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ScheduleQueueError("The PostgreSQL schedule queue is closed.")


__all__ = ["PostgreSQLScheduleQueue"]
