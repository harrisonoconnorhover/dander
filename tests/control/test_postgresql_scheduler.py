"""Five-field cron and PostgreSQL schedule producer/queue conformance."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.control.orchestration import ScheduleWakeup, TriggerKind, TriggerSpec
from dander.control.orchestration_serialization import deserialize_schedule_wakeup
from dander.control.postgresql_control_database import (
    CONTROL_SCHEMA_VERSION,
    PostgreSQLControlDatabase,
    PostgreSQLControlMigrator,
    PostgreSQLControlPool,
)
from dander.control.postgresql_schedule_queue import PostgreSQLScheduleQueue
from dander.control.postgresql_scheduler import (
    PostgreSQLScheduler,
    PostgreSQLSchedulerLeader,
    PostgreSQLScheduleSubmissionSource,
)
from dander.control.schedule_consumer import ScheduleQueueError
from dander.control.schedule_expression import (
    ScheduleExpressionError,
    iter_schedule_occurrences,
    parse_five_field_cron,
    require_iana_time_zone,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def _trigger(
    *,
    schedule: str = "* * * * *",
    time_zone: str = "UTC",
    enabled: bool = True,
) -> TriggerSpec:
    return TriggerSpec(
        trigger_id="daily-hdfs",
        kind=TriggerKind.SCHEDULE,
        plan_id="hdfs-join",
        plan_revision="a" * 64,
        enabled=enabled,
        schedule=schedule,
        time_zone=time_zone,
    )


def test_five_field_cron_supports_numeric_lists_ranges_and_steps() -> None:
    expression = parse_five_field_cron("0,15,30,45 1-5/2 1,13 * 0,7")

    assert expression.matches(datetime(2026, 9, 13, 3, 30, tzinfo=UTC))
    assert not expression.matches(datetime(2026, 9, 13, 4, 30, tzinfo=UTC))
    assert parse_five_field_cron("5/10 * * * *").matches(datetime(2026, 9, 1, 1, 55, tzinfo=UTC))
    assert parse_five_field_cron("*/20 * * * *").matches(datetime(2026, 9, 1, 1, 40, tzinfo=UTC))
    assert parse_five_field_cron("05 01 * * *").matches(datetime(2026, 9, 1, 1, 5, tzinfo=UTC))


@pytest.mark.parametrize(
    "expression",
    (
        "cron(0 6 * * ? *)",
        "0 6 * *",
        "0 6 * * * *",
        "0  6 * * *",
        "60 6 * * *",
        "0 24 * * *",
        "0 6 0 * *",
        "0 6 * 13 *",
        "0 6 * * 8",
        "*/0 6 * * *",
        "10-5 6 * * *",
        "0 six * * *",
    ),
)
def test_five_field_cron_rejects_non_boundary_syntax(expression: str) -> None:
    with pytest.raises(ScheduleExpressionError):
        parse_five_field_cron(expression)


def test_cron_uses_standard_day_of_month_day_of_week_or_semantics() -> None:
    expression = parse_five_field_cron("0 12 13 * 1")

    assert expression.matches(datetime(2026, 8, 10, 12, tzinfo=UTC))  # Monday.
    assert expression.matches(datetime(2026, 8, 13, 12, tzinfo=UTC))  # The 13th.
    assert not expression.matches(datetime(2026, 8, 12, 12, tzinfo=UTC))
    only_month_day = parse_five_field_cron("0 12 13 * *")
    assert not only_month_day.matches(datetime(2026, 8, 10, 12, tzinfo=UTC))
    only_week_day = parse_five_field_cron("0 12 * * 1")
    assert not only_week_day.matches(datetime(2026, 8, 13, 12, tzinfo=UTC))


def test_utc_iteration_handles_dst_gap_and_fold_without_local_ambiguity() -> None:
    new_york = require_iana_time_zone("America/New_York")
    missing = iter_schedule_occurrences(
        parse_five_field_cron("30 2 * * *"),
        new_york,
        after=datetime(2026, 3, 8, 6, 0, tzinfo=UTC),
        through=datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        max_minutes=120,
        max_occurrences=10,
    )
    folded = iter_schedule_occurrences(
        parse_five_field_cron("30 1 * * *"),
        new_york,
        after=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        through=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
        max_minutes=180,
        max_occurrences=10,
    )

    assert missing == ()
    assert folded == (
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    )
    with pytest.raises(ScheduleExpressionError, match="IANA"):
        require_iana_time_zone("Mars/Olympus_Mons")


def test_scheduler_rejects_legacy_six_field_and_non_iana_definitions() -> None:
    database = cast("PostgreSQLControlDatabase", object())
    queue = cast("PostgreSQLScheduleQueue", object())

    with pytest.raises(ValueError, match="definition"):
        PostgreSQLScheduler(database, queue, (_trigger(schedule="cron(0 6 * * ? *)"),))
    with pytest.raises(ValueError, match="definition"):
        PostgreSQLScheduler(database, queue, (_trigger(time_zone="Local"),))


class _OrderedComponent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self._name = name
        self._calls = calls

    def start(self) -> None:
        self._calls.append(f"start:{self._name}")

    def ready(self) -> bool:
        return True

    def close(self) -> None:
        self._calls.append(f"close:{self._name}")


def test_submission_source_stops_producer_before_consumer() -> None:
    calls: list[str] = []
    source = PostgreSQLScheduleSubmissionSource(
        cast("PostgreSQLScheduler", _OrderedComponent("producer", calls)),
        cast("object", _OrderedComponent("consumer", calls)),  # type: ignore[arg-type]
    )

    source.start()
    assert source.ready()
    source.close()

    assert calls == [
        "start:consumer",
        "start:producer",
        "close:producer",
        "close:consumer",
    ]


def test_scheduler_writes_on_the_session_that_owns_leadership() -> None:
    leader_connection = object()
    observed: list[object] = []

    class _Leader:
        def try_acquire(self) -> bool:
            return True

        def require_connection(self) -> object:
            return leader_connection

        def close(self) -> None:
            return None

    class _Queue:
        def enqueue_due(self, *_args: object, **kwargs: object) -> int:
            observed.append(kwargs["connection"])
            return 1

    scheduler = PostgreSQLScheduler(
        cast("PostgreSQLControlDatabase", object()),
        cast("PostgreSQLScheduleQueue", _Queue()),
        (_trigger(),),
        clock=lambda: NOW,
        initial_cursor=NOW - timedelta(minutes=1),
    )
    scheduler._leader = cast("PostgreSQLSchedulerLeader", _Leader())  # noqa: SLF001

    assert scheduler.tick_once() == 1
    assert observed == [leader_connection]


@pytest.fixture
def postgresql_schedule_control() -> Iterator[
    tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str]
]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    schema_name = f"dander_schedule_{uuid.uuid4().hex}"
    pool = cast(
        "PostgreSQLControlPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=2,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=5)
    database = PostgreSQLControlDatabase(pool=pool, schema_name=schema_name)
    try:
        yield database, pool, schema_name
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
        pool.close()


def test_postgresql_schedule_leader_is_one_dedicated_session(
    postgresql_schedule_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_schedule_control
    PostgreSQLControlMigrator(database).migrate()
    first = PostgreSQLSchedulerLeader(database)
    second = PostgreSQLSchedulerLeader(database)

    assert first.try_acquire() is True
    assert second.try_acquire() is False
    first.close()
    assert second.try_acquire() is True
    second.close()


def test_postgresql_schedule_queue_deduplicates_leases_and_dead_letters(
    postgresql_schedule_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_schedule_control
    assert PostgreSQLControlMigrator(database).migrate() == CONTROL_SCHEMA_VERSION
    queue = PostgreSQLScheduleQueue(
        database,
        visibility_timeout_seconds=30,
        max_deliveries=2,
    )
    trigger = _trigger()

    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW,
            initial_cursor=NOW - timedelta(minutes=1),
        )
        == 1
    )
    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW,
            initial_cursor=NOW - timedelta(minutes=1),
        )
        == 0
    )
    first = queue.receive()
    assert len(first) == 1
    assert deserialize_schedule_wakeup(first[0].body) == ScheduleWakeup(
        trigger.trigger_id,
        trigger.plan_revision,
        NOW,
    )
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET lease_until = clock_timestamp() - INTERVAL '1 second'").format(
                database.relation("dander_schedule_queue")
            )
        )
    with pytest.raises(ScheduleQueueError, match="stale"):
        queue.delete(first[0].receipt_handle)
    second = queue.receive()
    assert len(second) == 1
    assert second[0].receipt_handle != first[0].receipt_handle
    with pytest.raises(ScheduleQueueError, match="stale"):
        queue.delete(first[0].receipt_handle)
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET lease_until = clock_timestamp() - INTERVAL '1 second'").format(
                database.relation("dander_schedule_queue")
            )
        )
    assert queue.receive() == ()
    with pool.connection() as connection:
        dead = connection.execute(
            sql.SQL("SELECT count(*) AS count FROM {} WHERE dead_lettered_at IS NOT NULL").format(
                database.relation("dander_schedule_queue")
            )
        ).fetchone()
    assert dead is not None and dead["count"] == 1
    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW + timedelta(minutes=1),
            initial_cursor=NOW - timedelta(minutes=1),
        )
        == 1
    )
    final = queue.receive()
    assert len(final) == 1
    queue.delete(final[0].receipt_handle)
    assert queue.receive() == ()
    queue.close()
    assert not pool.closed


def test_postgresql_schedule_edit_cuts_over_without_historical_reinterpretation(
    postgresql_schedule_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_schedule_control
    PostgreSQLControlMigrator(database).migrate()
    queue = PostgreSQLScheduleQueue(database)
    original = _trigger(schedule="0 * * * *")
    changed = TriggerSpec(
        trigger_id=original.trigger_id,
        kind=original.kind,
        plan_id=original.plan_id,
        plan_revision="b" * 64,
        enabled=True,
        schedule="* * * * *",
        time_zone="UTC",
    )

    assert (
        queue.enqueue_due(
            (original,),
            through=NOW,
            initial_cursor=NOW - timedelta(hours=1),
        )
        == 1
    )
    assert (
        queue.enqueue_due(
            (changed,),
            through=NOW + timedelta(minutes=5),
            initial_cursor=NOW - timedelta(hours=1),
        )
        == 0
    )
    assert (
        queue.enqueue_due(
            (changed,),
            through=NOW + timedelta(minutes=6),
            initial_cursor=NOW - timedelta(hours=1),
        )
        == 1
    )

    messages = queue.receive()
    wakeups = tuple(deserialize_schedule_wakeup(item.body) for item in messages)
    assert wakeups == (
        ScheduleWakeup(original.trigger_id, original.plan_revision, NOW),
        ScheduleWakeup(changed.trigger_id, changed.plan_revision, NOW + timedelta(minutes=6)),
    )


def test_postgresql_schedule_catch_up_advances_in_bounded_committed_pages(
    postgresql_schedule_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_schedule_control
    PostgreSQLControlMigrator(database).migrate()
    queue = PostgreSQLScheduleQueue(database)
    trigger = _trigger()
    start = NOW - timedelta(minutes=5)

    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW,
            initial_cursor=start,
            max_scan_minutes=10,
            max_occurrences=2,
        )
        == 2
    )
    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW,
            initial_cursor=start,
            max_scan_minutes=10,
            max_occurrences=2,
        )
        == 2
    )
    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW,
            initial_cursor=start,
            max_scan_minutes=10,
            max_occurrences=2,
        )
        == 1
    )
    with pool.connection() as connection:
        row = connection.execute(
            sql.SQL("SELECT cursor_utc FROM {} WHERE trigger_id = %s").format(
                database.relation("dander_schedule_cursors")
            ),
            (trigger.trigger_id,),
        ).fetchone()
    assert row is not None and row["cursor_utc"] == NOW


def test_postgresql_schedule_queue_quarantines_corruption_without_blocking_valid_rows(
    postgresql_schedule_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_schedule_control
    PostgreSQLControlMigrator(database).migrate()
    queue = PostgreSQLScheduleQueue(database)
    trigger = _trigger()
    assert (
        queue.enqueue_due(
            (trigger,),
            through=NOW + timedelta(minutes=1),
            initial_cursor=NOW - timedelta(minutes=1),
        )
        == 2
    )
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET body = %s WHERE scheduled_occurrence = %s").format(
                database.relation("dander_schedule_queue")
            ),
            (b"{}", NOW),
        )

    messages = queue.receive()
    assert len(messages) == 1
    assert deserialize_schedule_wakeup(messages[0].body).scheduled_occurrence == NOW + timedelta(
        minutes=1
    )
    with pool.connection() as connection:
        row = connection.execute(
            sql.SQL("SELECT count(*) AS count FROM {} WHERE dead_lettered_at IS NOT NULL").format(
                database.relation("dander_schedule_queue")
            )
        ).fetchone()
    assert row is not None and row["count"] == 1
