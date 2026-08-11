"""Bounded, row-sanitized Google credential refresh probe for hosted launchers."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.cloud import bigquery

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
PROBE_SCHEMA = "io.dander.portability.identity-probe/v1"


class GoogleRefreshProbeError(RuntimeError):
    """The live identity-refresh proof did not satisfy its bounded contract."""


class ExpiringCredentials(Protocol):
    expiry: datetime | None


class QueryClient(Protocol):
    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        """Return a bounded count from the proof relation."""


class BigQueryProbeClient:
    """Keep proof rows and credentials out of Dander's emitted evidence."""

    def __init__(self, client: bigquery.Client) -> None:
        self._client = client

    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        validate_probe_target(project=project, dataset=dataset, table=table)
        query = f"SELECT COUNT(*) AS row_count FROM `{project}.{dataset}.{table}`"
        row = next(iter(self._client.query(query).result(max_results=1)), None)
        if row is None:
            raise GoogleRefreshProbeError("BigQuery proof query returned no result")
        value = row[0]
        if not isinstance(value, int) or value < 0:
            raise GoogleRefreshProbeError("BigQuery proof query returned an invalid count")
        return value


def run_google_refresh_probe(
    *,
    credentials: ExpiringCredentials,
    client: QueryClient,
    project: str,
    dataset: str,
    table: str,
    max_wait_seconds: int,
    refresh_margin_seconds: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict[str, object]], None],
) -> None:
    """Query before and after expiry, requiring the same credentials object to refresh."""
    validate_probe_target(project=project, dataset=dataset, table=table)
    if not 1 <= max_wait_seconds <= 1_800 or not 0 <= refresh_margin_seconds <= 60:
        raise GoogleRefreshProbeError("Credential refresh proof bounds are invalid")
    first_count = client.query_count(project=project, dataset=dataset, table=table)
    first_expiry = _expiry(credentials)
    first_time = _utc(now())
    wait_seconds = max(0.0, (first_expiry - first_time).total_seconds()) + refresh_margin_seconds
    if wait_seconds > max_wait_seconds:
        raise GoogleRefreshProbeError("Issued credential lifetime exceeds the bounded proof window")
    emit(
        {
            "schema": PROBE_SCHEMA,
            "event": "query.completed",
            "sequence": 1,
            "row_count": first_count,
            "credential_expiry": first_expiry.isoformat(),
        }
    )

    sleep(wait_seconds)

    second_count = client.query_count(project=project, dataset=dataset, table=table)
    second_expiry = _expiry(credentials)
    if second_expiry <= first_expiry:
        raise GoogleRefreshProbeError("Google credentials did not refresh after expiry")
    emit(
        {
            "schema": PROBE_SCHEMA,
            "event": "query.completed",
            "sequence": 2,
            "row_count": second_count,
            "credential_expiry": second_expiry.isoformat(),
        }
    )
    emit(
        {
            "schema": PROBE_SCHEMA,
            "event": "credential.refresh_observed",
            "previous_expiry": first_expiry.isoformat(),
            "refreshed_expiry": second_expiry.isoformat(),
        }
    )


def validate_probe_target(*, project: str, dataset: str, table: str) -> None:
    if _PROJECT_ID.fullmatch(project) is None or not all(
        _IDENTIFIER.fullmatch(value) for value in (dataset, table)
    ):
        raise GoogleRefreshProbeError("BigQuery proof target is invalid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _expiry(credentials: ExpiringCredentials) -> datetime:
    if credentials.expiry is None:
        raise GoogleRefreshProbeError("Google credentials did not expose an expiry")
    return _utc(credentials.expiry)


__all__ = [
    "BigQueryProbeClient",
    "ExpiringCredentials",
    "GoogleRefreshProbeError",
    "PROBE_SCHEMA",
    "run_google_refresh_probe",
    "validate_probe_target",
]
