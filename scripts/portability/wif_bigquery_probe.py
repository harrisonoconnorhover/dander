"""Prove that an AWS task can refresh keyless Google credentials during one process."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import time
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import google.auth
from google.cloud import bigquery

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_SCHEMA = "io.dander.portability.identity-probe/v1"
_ECS_CREDENTIALS_PATH = re.compile(r"^/v2/credentials/[A-Za-z0-9-]{16,128}$")
_ECS_CREDENTIALS_ORIGIN = "http://169.254.170.2"
_TEMPORARY_ACCESS_KEY = re.compile(r"^ASIA[A-Z0-9]{16}$")


class ProbeError(RuntimeError):
    """Raised when the live identity-refresh proof does not satisfy its contract."""


class ExpiringCredentials(Protocol):
    expiry: datetime | None


class QueryClient(Protocol):
    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        """Return a bounded count from the proof relation."""


class CredentialFetcher(Protocol):
    def __call__(self, url: str) -> object:
        """Return one parsed ECS task-credential response."""


def _fetch_ecs_credentials(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise ProbeError("ECS task credentials were unavailable")
        return json.load(response)


def prepare_fargate_task_credentials(
    *,
    environ: MutableMapping[str, str] = os.environ,
    fetch: CredentialFetcher = _fetch_ecs_credentials,
) -> bool:
    """Expose short-lived ECS task credentials to Google Auth without persisting them."""
    existing = tuple(
        environ.get(name)
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    )
    if all(existing):
        return False
    if any(existing):
        raise ProbeError("AWS credential environment is incomplete")

    relative_uri = environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if relative_uri is None:
        return False
    if _ECS_CREDENTIALS_PATH.fullmatch(relative_uri) is None:
        raise ProbeError("ECS task credential endpoint is invalid")

    document = fetch(f"{_ECS_CREDENTIALS_ORIGIN}{relative_uri}")
    if not isinstance(document, dict):
        raise ProbeError("ECS task credentials were invalid")
    access_key = document.get("AccessKeyId")
    secret_key = document.get("SecretAccessKey")
    session_token = document.get("Token")
    if (
        not isinstance(access_key, str)
        or _TEMPORARY_ACCESS_KEY.fullmatch(access_key) is None
        or not isinstance(secret_key, str)
        or not secret_key
        or not isinstance(session_token, str)
        or not session_token
    ):
        raise ProbeError("ECS task credentials were invalid")

    environ["AWS_ACCESS_KEY_ID"] = access_key
    environ["AWS_SECRET_ACCESS_KEY"] = secret_key
    environ["AWS_SESSION_TOKEN"] = session_token
    return True


class BigQueryProbeClient:
    """Small adapter that keeps the exact proof query out of logs and task configuration."""

    def __init__(self, client: bigquery.Client) -> None:
        self._client = client

    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        if not _PROJECT_ID.fullmatch(project) or not all(
            _IDENTIFIER.fullmatch(value) for value in (dataset, table)
        ):
            raise ProbeError("BigQuery proof target is invalid")
        query = f"SELECT COUNT(*) AS row_count FROM `{project}.{dataset}.{table}`"
        row = next(iter(self._client.query(query).result(max_results=1)), None)
        if row is None:
            raise ProbeError("BigQuery proof query returned no result")
        value = row[0]
        if not isinstance(value, int) or value < 0:
            raise ProbeError("BigQuery proof query returned an invalid count")
        return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _expiry(credentials: ExpiringCredentials) -> datetime:
    if credentials.expiry is None:
        raise ProbeError("Google credentials did not expose an expiry")
    return _utc(credentials.expiry)


def run_probe(
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
    first_count = client.query_count(project=project, dataset=dataset, table=table)
    first_expiry = _expiry(credentials)
    first_time = _utc(now())
    wait_seconds = max(0.0, (first_expiry - first_time).total_seconds()) + refresh_margin_seconds
    if wait_seconds > max_wait_seconds:
        raise ProbeError("Issued credential lifetime exceeds the bounded proof window")
    emit(
        {
            "schema": _SCHEMA,
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
        raise ProbeError("Google credentials did not refresh after expiry")
    emit(
        {
            "schema": _SCHEMA,
            "event": "query.completed",
            "sequence": 2,
            "row_count": second_count,
            "credential_expiry": second_expiry.isoformat(),
        }
    )
    emit(
        {
            "schema": _SCHEMA,
            "event": "credential.refresh_observed",
            "previous_expiry": first_expiry.isoformat(),
            "refreshed_expiry": second_expiry.isoformat(),
        }
    )


def _emit(record: dict[str, object]) -> None:
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--max-wait-seconds", type=int, default=900)
    parser.add_argument("--refresh-margin-seconds", type=int, default=15)
    args = parser.parse_args()
    if not _PROJECT_ID.fullmatch(args.project) or not all(
        _IDENTIFIER.fullmatch(value) for value in (args.dataset, args.table)
    ):
        _emit({"schema": _SCHEMA, "event": "probe.failed", "failure_code": "invalid_target"})
        return 2
    try:
        task_credentials_loaded = prepare_fargate_task_credentials()
        _emit(
            {
                "schema": _SCHEMA,
                "event": "runtime.observed",
                "architecture": platform.machine().lower(),
                "aws_credential_source": (
                    "ecs_task_role" if task_credentials_loaded else "aws_default_chain"
                ),
            }
        )
        credentials, _ = google.auth.default(scopes=_SCOPES)
        client = BigQueryProbeClient(bigquery.Client(project=args.project, credentials=credentials))
        run_probe(
            credentials=credentials,
            client=client,
            project=args.project,
            dataset=args.dataset,
            table=args.table,
            max_wait_seconds=args.max_wait_seconds,
            refresh_margin_seconds=args.refresh_margin_seconds,
            emit=_emit,
        )
    except Exception as error:  # noqa: BLE001 - the probe must sanitize every library failure
        _emit(
            {
                "schema": _SCHEMA,
                "event": "probe.failed",
                "failure_code": "identity_or_query_failed",
                "failure_type": type(error).__name__,
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
