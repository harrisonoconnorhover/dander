"""Shared state-lease and destination-publication fencing contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from google.cloud import bigquery

_TABLE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
_AUTHORITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")


@dataclass(frozen=True)
class FencingToken:
    """One run's ownership token issued by a durable-state authority."""

    lease_table: str | None
    pipeline_id: str
    run_id: str
    token: int
    authority_id: str | None = None
    authority_epoch: int = 1

    def __post_init__(self) -> None:
        if self.lease_table is not None and not _TABLE_ID.fullmatch(self.lease_table):
            raise ValueError("Invalid BigQuery lease table")
        if not self.pipeline_id or not self.run_id:
            raise ValueError("Fencing identifiers must be non-empty")
        if isinstance(self.token, bool) or self.token <= 0:
            raise ValueError("Fencing token must be a positive integer")
        if self.authority_id is None and self.lease_table is None:
            raise ValueError("Fencing token requires an authority id")
        if self.authority_id is not None and not _AUTHORITY_ID.fullmatch(self.authority_id):
            raise ValueError("Invalid fencing authority id")
        if isinstance(self.authority_epoch, bool) or self.authority_epoch <= 0:
            raise ValueError("Fencing authority epoch must be a positive integer")

    @property
    def resolved_authority_id(self) -> str:
        """Return the explicit authority or the legacy BigQuery deployment identity."""
        if self.authority_id is not None:
            return self.authority_id
        assert self.lease_table is not None
        return f"bigquery:{self.lease_table.rsplit('.', 1)[0]}"


@dataclass(frozen=True)
class TargetFence:
    """One destination target claimed by an exact state lease owner."""

    fence_table: str
    target_id: str
    authority_id: str
    authority_epoch: int
    pipeline_id: str
    run_id: str
    token: int

    def __post_init__(self) -> None:
        if not self.fence_table or not self.target_id:
            raise ValueError("Target fence identifiers must be non-empty")
        if not _AUTHORITY_ID.fullmatch(self.authority_id):
            raise ValueError("Invalid target-fence authority id")
        if isinstance(self.authority_epoch, bool) or self.authority_epoch <= 0:
            raise ValueError("Target-fence authority epoch must be a positive integer")
        if not self.pipeline_id or not self.run_id:
            raise ValueError("Target-fence run identifiers must be non-empty")
        if isinstance(self.token, bool) or self.token <= 0:
            raise ValueError("Target-fence token must be a positive integer")


class TargetFenceLostError(RuntimeError):
    """Raised when a destination rejects a stale or foreign publication owner."""


class OwnershipGuard(Protocol):
    """Verify current lease ownership immediately before a finalizer."""

    @property
    def fence(self) -> FencingToken | None:
        """Return the hosted BigQuery fence, if this backend supports one."""

    def verify(self) -> None:
        """Renew ownership or fail closed."""


def fenced_dml(statement: str, fence: FencingToken) -> str:
    """Fence one DML finalizer by touching its owned lease row transactionally."""
    finalizer = statement.strip().removesuffix(";")
    return f"BEGIN TRANSACTION;\n{fencing_touch_sql(fence)}\n{finalizer};\nCOMMIT TRANSACTION;"


def fencing_touch_sql(fence: FencingToken) -> str:
    """Return the conditional lease-row DML and assertion for an existing transaction."""
    if fence.lease_table is None:
        raise ValueError("State-side BigQuery fencing requires a BigQuery lease table")
    return (
        f"UPDATE `{fence.lease_table}`\n"
        "SET heartbeat_at = heartbeat_at\n"
        "WHERE pipeline_id = @dander_pipeline_id\n"
        "  AND run_id = @dander_run_id\n"
        "  AND fencing_token = @dander_fencing_token\n"
        "  AND lease_expires_at > CURRENT_TIMESTAMP();\n"
        "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost';"
    )


def fencing_job_config(fence: FencingToken) -> bigquery.QueryJobConfig:
    """Bind non-secret lease identity to a fenced BigQuery script."""
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "dander_pipeline_id",
                "STRING",
                fence.pipeline_id,
            ),
            bigquery.ScalarQueryParameter("dander_run_id", "STRING", fence.run_id),
            bigquery.ScalarQueryParameter(
                "dander_fencing_token",
                "INT64",
                fence.token,
            ),
        ]
    )
