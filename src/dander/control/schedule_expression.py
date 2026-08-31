"""Bounded five-field cron evaluation for the PostgreSQL Control scheduler.

Occurrences are iterated in UTC and matched after projection into the configured IANA time zone.
That keeps every result unambiguous across daylight-saving gaps and folds without inventing local
timestamps that never existed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_INTEGER = re.compile(r"^[0-9]+$")
_MAX_EXPRESSION_LENGTH = 256


class ScheduleExpressionError(ValueError):
    """A PostgreSQL scheduler expression or time zone is unsupported."""


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    unrestricted: bool


@dataclass(frozen=True, slots=True)
class FiveFieldCron:
    """One parsed numeric minute/hour/day/month/week cron expression."""

    expression: str
    minute: _CronField
    hour: _CronField
    day_of_month: _CronField
    month: _CronField
    day_of_week: _CronField

    def matches(self, local: datetime) -> bool:
        """Return whether one aware local minute matches standard cron DOM/DOW semantics."""
        if local.tzinfo is None or local.utcoffset() is None:
            raise ScheduleExpressionError("Cron matching requires an aware local timestamp.")
        cron_weekday = (local.weekday() + 1) % 7
        minute_hour_month = (
            local.minute in self.minute.values
            and local.hour in self.hour.values
            and local.month in self.month.values
        )
        if not minute_hour_month:
            return False
        month_day_matches = local.day in self.day_of_month.values
        week_day_matches = cron_weekday in self.day_of_week.values
        if self.day_of_month.unrestricted and self.day_of_week.unrestricted:
            return True
        if self.day_of_month.unrestricted:
            return week_day_matches
        if self.day_of_week.unrestricted:
            return month_day_matches
        return month_day_matches or week_day_matches


def parse_five_field_cron(expression: str) -> FiveFieldCron:
    """Parse the exact numeric five-field subset supported by hosted PostgreSQL Control."""
    if (
        not isinstance(expression, str)
        or not expression
        or len(expression) > _MAX_EXPRESSION_LENGTH
        or expression.strip() != expression
        or "  " in expression
        or "\t" in expression
        or "\n" in expression
        or "\r" in expression
    ):
        raise ScheduleExpressionError(
            "A schedule must be a canonical five-field cron expression separated by spaces."
        )
    parts = expression.split(" ")
    if len(parts) != 5:
        raise ScheduleExpressionError("The PostgreSQL scheduler accepts exactly five cron fields.")
    minute = _parse_field(parts[0], minimum=0, maximum=59, label="minute")
    hour = _parse_field(parts[1], minimum=0, maximum=23, label="hour")
    day_of_month = _parse_field(parts[2], minimum=1, maximum=31, label="day of month")
    month = _parse_field(parts[3], minimum=1, maximum=12, label="month")
    day_of_week = _parse_field(
        parts[4],
        minimum=0,
        maximum=7,
        label="day of week",
        sunday_alias=True,
    )
    return FiveFieldCron(
        expression=expression,
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month=month,
        day_of_week=day_of_week,
    )


def require_iana_time_zone(name: str) -> ZoneInfo:
    """Resolve one non-path IANA ZoneInfo key without falling back to the host time zone."""
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 255
        or name.strip() != name
        or name.startswith("/")
        or ".." in name.split("/")
    ):
        raise ScheduleExpressionError("The schedule time zone must be a valid IANA identifier.")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ScheduleExpressionError(
            "The schedule time zone must be a valid IANA identifier."
        ) from error


def iter_schedule_occurrences(
    schedule: FiveFieldCron,
    time_zone: ZoneInfo,
    *,
    after: datetime,
    through: datetime,
    max_minutes: int,
    max_occurrences: int,
) -> tuple[datetime, ...]:
    """Return matching UTC minutes in ``(after, through]`` within explicit work bounds."""
    _require_utc_minute(after, label="schedule cursor")
    _require_utc_minute(through, label="schedule horizon")
    if through < after:
        raise ScheduleExpressionError("The schedule horizon cannot precede its cursor.")
    if isinstance(max_minutes, bool) or not 1 <= max_minutes <= 525_600:
        raise ScheduleExpressionError("The cron scan bound must be 1 to 525600 minutes.")
    if isinstance(max_occurrences, bool) or not 1 <= max_occurrences <= 10_000:
        raise ScheduleExpressionError("The occurrence bound must be 1 to 10000.")
    span_minutes = int((through - after).total_seconds() // 60)
    if span_minutes > max_minutes:
        raise ScheduleExpressionError("The requested cron scan exceeds its bounded horizon.")
    matches: list[datetime] = []
    candidate = after + timedelta(minutes=1)
    while candidate <= through:
        if schedule.matches(candidate.astimezone(time_zone)):
            matches.append(candidate)
            if len(matches) > max_occurrences:
                raise ScheduleExpressionError("The cron scan exceeds its occurrence bound.")
        candidate += timedelta(minutes=1)
    return tuple(matches)


def floor_utc_minute(value: datetime) -> datetime:
    """Return one UTC-aware timestamp truncated to its minute boundary."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleExpressionError("The scheduler clock must return an aware timestamp.")
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _parse_field(
    value: str,
    *,
    minimum: int,
    maximum: int,
    label: str,
    sunday_alias: bool = False,
) -> _CronField:
    unrestricted = value == "*"
    values: set[int] = set()
    terms = value.split(",")
    if not terms or any(not term for term in terms):
        raise ScheduleExpressionError(f"The cron {label} field is invalid.")
    for term in terms:
        base, step, stepped = _split_step(term, label=label)
        start, end = _field_range(
            base,
            minimum=minimum,
            maximum=maximum,
            label=label,
            extend_single=stepped,
        )
        if step > maximum - minimum + 1:
            raise ScheduleExpressionError(f"The cron {label} step is out of range.")
        values.update(range(start, end + 1, step))
    if sunday_alias and 7 in values:
        values.remove(7)
        values.add(0)
    return _CronField(values=frozenset(values), unrestricted=unrestricted)


def _split_step(term: str, *, label: str) -> tuple[str, int, bool]:
    parts = term.split("/")
    if len(parts) > 2 or not parts[0]:
        raise ScheduleExpressionError(f"The cron {label} field is invalid.")
    if len(parts) == 1:
        return parts[0], 1, False
    if _INTEGER.fullmatch(parts[1]) is None:
        raise ScheduleExpressionError(f"The cron {label} step is invalid.")
    step = int(parts[1])
    if step < 1:
        raise ScheduleExpressionError(f"The cron {label} step must be positive.")
    return parts[0], step, True


def _field_range(
    value: str,
    *,
    minimum: int,
    maximum: int,
    label: str,
    extend_single: bool,
) -> tuple[int, int]:
    if value == "*":
        return minimum, maximum
    bounds = value.split("-")
    if len(bounds) > 2 or any(_INTEGER.fullmatch(bound) is None for bound in bounds):
        raise ScheduleExpressionError(f"The cron {label} field is invalid.")
    start = int(bounds[0])
    end = int(bounds[1]) if len(bounds) == 2 else (maximum if extend_single else start)
    if not minimum <= start <= maximum or not minimum <= end <= maximum or start > end:
        raise ScheduleExpressionError(f"The cron {label} field is out of range.")
    return start, end


def _require_utc_minute(value: datetime, *, label: str) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
        or value.second != 0
        or value.microsecond != 0
    ):
        raise ScheduleExpressionError(f"The {label} must be an exact UTC minute.")


__all__ = [
    "FiveFieldCron",
    "ScheduleExpressionError",
    "floor_utc_minute",
    "iter_schedule_occurrences",
    "parse_five_field_cron",
    "require_iana_time_zone",
]
