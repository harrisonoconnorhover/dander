"""Credential refresh proof emits only bounded, sanitized lifecycle facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dander.identity.refresh_probe import GoogleRefreshProbeError, run_google_refresh_probe

_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class _Credentials:
    def __init__(self) -> None:
        self.expiry: datetime | None = _NOW + timedelta(seconds=10)


class _Client:
    def __init__(self, credentials: _Credentials) -> None:
        self.credentials = credentials
        self.calls = 0

    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        assert (project, dataset, table) == ("unit-project", "raw", "proof_rows")
        self.calls += 1
        if self.calls == 2:
            self.credentials.expiry = _NOW + timedelta(minutes=10)
        return 3


def test_refresh_probe_requires_later_expiry_and_emits_no_rows_or_tokens() -> None:
    credentials = _Credentials()
    client = _Client(credentials)
    events: list[dict[str, object]] = []
    waits: list[float] = []

    run_google_refresh_probe(
        credentials=credentials,
        client=client,
        project="unit-project",
        dataset="raw",
        table="proof_rows",
        max_wait_seconds=30,
        refresh_margin_seconds=5,
        now=lambda: _NOW,
        sleep=waits.append,
        emit=events.append,
    )

    assert waits == [15.0]
    assert [event["event"] for event in events] == [
        "query.completed",
        "query.completed",
        "credential.refresh_observed",
    ]
    assert [event.get("row_count") for event in events[:2]] == [3, 3]
    serialized = repr(events).lower()
    assert "token" not in serialized
    assert "select" not in serialized
    assert "proof_rows" not in serialized


def test_refresh_probe_rejects_excessive_wait_without_sleeping() -> None:
    credentials = _Credentials()
    credentials.expiry = _NOW + timedelta(minutes=20)

    with pytest.raises(GoogleRefreshProbeError, match="bounded proof window"):
        run_google_refresh_probe(
            credentials=credentials,
            client=_Client(credentials),
            project="unit-project",
            dataset="raw",
            table="proof_rows",
            max_wait_seconds=900,
            refresh_margin_seconds=15,
            now=lambda: _NOW,
            sleep=lambda _seconds: pytest.fail("probe must not wait"),
            emit=lambda _event: None,
        )


@pytest.mark.parametrize(
    ("project", "dataset", "table"),
    [
        ("UPPERCASE", "raw", "proof_rows"),
        ("unit-project", "raw;drop", "proof_rows"),
        ("unit-project", "raw", "proof.rows"),
    ],
)
def test_refresh_probe_rejects_invalid_targets(
    project: str,
    dataset: str,
    table: str,
) -> None:
    credentials = _Credentials()

    with pytest.raises(GoogleRefreshProbeError, match="target"):
        run_google_refresh_probe(
            credentials=credentials,
            client=_Client(credentials),
            project=project,
            dataset=dataset,
            table=table,
            max_wait_seconds=30,
            refresh_margin_seconds=5,
            emit=lambda _event: None,
        )
