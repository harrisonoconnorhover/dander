"""Exclusive lease, heartbeat, and fencing-token contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dander.state import (
    BigQueryLeaseStore,
    LeaseHandle,
    LeaseHeartbeat,
    LeaseLostError,
    LeaseStore,
    SqliteLeaseStore,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from google.cloud import bigquery


class _Job:
    def __init__(
        self,
        *,
        affected: int | None = None,
        rows: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.num_dml_affected_rows = affected
        self._rows = list(rows)

    def result(self) -> list[Mapping[str, Any]]:
        return self._rows


class _Client:
    def __init__(self, *, claim_affected: int = 1, token: int = 7) -> None:
        self.claim_affected = claim_affected
        self.token = token
        self.heartbeat_affected = 1
        self.release_affected = 1
        self.queries: list[str] = []
        self.configs: list[bigquery.QueryJobConfig | None] = []

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        self.queries.append(query)
        self.configs.append(job_config)
        if query.startswith("SELECT fencing_token"):
            return _Job(rows=[{"fencing_token": self.token}])
        if "fencing_token = fencing_token + 1" in query:
            return _Job(affected=self.claim_affected)
        if "SET run_id = NULL" in query:
            return _Job(affected=self.release_affected)
        if query.startswith("UPDATE"):
            return _Job(affected=self.heartbeat_affected)
        return _Job()


def test_bigquery_lease_claims_with_conditional_dml_and_builds_fence() -> None:
    client = _Client()
    store = BigQueryLeaseStore(
        project="unit-project",
        dataset="meta",
        client=client,
    )

    lease = store.acquire("greenhouse_jobs", "run-one")

    assert lease is not None
    assert lease.fencing_token == 7
    assert lease.fence is not None
    assert lease.fence.lease_table is not None
    assert lease.fence.lease_table.startswith("unit-project.meta._dander_lease_")
    claim = next(query for query in client.queries if "fencing_token + 1" in query)
    assert "lease_expires_at <= CURRENT_TIMESTAMP()" in claim
    assert "SELECT" not in claim.split("WHERE", 1)[1]


def test_bigquery_leases_isolate_pipeline_mutations_in_separate_tables() -> None:
    client = _Client()
    store = BigQueryLeaseStore(
        project="unit-project",
        dataset="meta",
        client=client,
    )

    greenhouse = store.acquire("greenhouse_jobs", "run-one")
    hubspot = store.acquire("hubspot_companies", "run-two")

    assert greenhouse is not None and greenhouse.fence is not None
    assert hubspot is not None and hubspot.fence is not None
    assert greenhouse.fence.lease_table is not None
    assert hubspot.fence.lease_table is not None
    assert greenhouse.fence.lease_table != hubspot.fence.lease_table
    assert greenhouse.fence.lease_table.startswith("unit-project.meta._dander_lease_")
    assert hubspot.fence.lease_table.startswith("unit-project.meta._dander_lease_")


@pytest.mark.parametrize("affected", [0, 2])
def test_bigquery_lease_fails_closed_unless_exactly_one_row_is_claimed(affected: int) -> None:
    client = _Client(claim_affected=affected)
    store = BigQueryLeaseStore(
        project="unit-project",
        dataset="meta",
        client=client,
    )

    assert store.acquire("greenhouse_jobs", "run-two") is None
    assert all(not query.startswith("SELECT fencing_token") for query in client.queries)


def test_bigquery_heartbeat_and_release_match_run_and_fencing_token() -> None:
    client = _Client()
    store = BigQueryLeaseStore(
        project="unit-project",
        dataset="meta",
        client=client,
    )
    lease = store.acquire("greenhouse_jobs", "run-one")
    assert lease is not None

    assert store.heartbeat(lease)
    assert store.release(lease)

    heartbeat = next(
        query
        for query in client.queries
        if query.startswith("UPDATE") and "lease_expires_at >" in query
    )
    release = next(query for query in client.queries if "SET run_id = NULL" in query)
    for statement in (heartbeat, release):
        assert "pipeline_id = @pipeline_id" in statement
        assert "run_id = @run_id" in statement
        assert "fencing_token = @fencing_token" in statement


def test_sqlite_lease_skips_overlap_and_increments_token_after_release(tmp_path: Path) -> None:
    store = SqliteLeaseStore(tmp_path / "state.db")

    first = store.acquire("hubspot_companies", "run-one")
    assert first is not None
    assert store.acquire("hubspot_companies", "run-two") is None
    assert store.heartbeat(first)
    assert store.release(first)

    second = store.acquire("hubspot_companies", "run-two")
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
    assert not store.heartbeat(first)
    assert not store.release(first)


class _HeartbeatStore(LeaseStore):
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = outcomes
        self.released = False

    @property
    def lease_seconds(self) -> int:
        return 30

    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        return LeaseHandle(pipeline_id, run_id, 1, self.lease_seconds)

    def heartbeat(self, lease: LeaseHandle) -> bool:
        return self._outcomes.pop(0)

    def release(self, lease: LeaseHandle) -> bool:
        self.released = True
        return True


def test_heartbeat_guard_fails_closed_after_ownership_loss() -> None:
    store = _HeartbeatStore([True, False])
    lease = store.acquire("example", "run")
    assert lease is not None

    with LeaseHeartbeat(store, lease, background=False) as guard:
        guard.verify()
        with pytest.raises(LeaseLostError, match="ownership was lost"):
            guard.verify()
        with pytest.raises(LeaseLostError, match="ownership was lost"):
            guard.verify()

    assert store.released
