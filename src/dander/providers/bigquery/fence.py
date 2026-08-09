"""BigQuery destination-side target fencing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from google.cloud import bigquery

from dander._bigquery_retry import run_mutation_with_retry
from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.identity import google_client_options
from dander.warehouse.runtime import PreparedWarehouseStatement

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dander.warehouse.contracts import RelationRef

_TABLE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


class _FenceJob(Protocol):
    num_dml_affected_rows: int | None

    def result(self) -> Iterable[object]:
        """Wait for the destination-fence mutation."""
        ...


class _FenceClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FenceJob:
        """Submit one BigQuery fence statement."""
        ...


@dataclass(slots=True)
class BigQueryTargetFence:
    """Claim and verify a target using a BigQuery destination-side ledger."""

    project: str
    client: _FenceClient | None = None

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        """Atomically accept a newer token or an exact idempotent retry."""
        if target.catalog != self.project:
            raise ValueError("BigQuery target fence must use the runtime project")
        table = f"{target.catalog}.{target.namespace}._dander_target_commits"
        target_id = ".".join(target.coordinates)
        client = self._required_client()
        run_mutation_with_retry(lambda: client.query(_target_fence_table_sql(table)))
        claim = run_mutation_with_retry(
            lambda: client.query(
                _target_claim_sql(table),
                job_config=_target_fence_config(target_id, fence),
            )
        )
        if claim.num_dml_affected_rows != 1:
            raise TargetFenceLostError(
                f"Destination target {target_id!r} rejected stale publication ownership"
            )
        return TargetFence(
            fence_table=table,
            target_id=target_id,
            authority_id=fence.resolved_authority_id,
            authority_epoch=fence.authority_epoch,
            pipeline_id=fence.pipeline_id,
            run_id=fence.run_id,
            token=fence.token,
        )

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        """Verify, publish, and record completion in one BigQuery transaction."""
        if not _TABLE_ID.fullmatch(fence.fence_table) or not fence.fence_table.startswith(
            f"{self.project}."
        ):
            raise ValueError("BigQuery target fence belongs to another runtime project")
        finalizer = statement.strip().removesuffix(";")
        return PreparedWarehouseStatement(
            sql=(
                "BEGIN TRANSACTION;\n"
                f"{_target_touch_sql(fence)}\n"
                f"{finalizer};\n"
                f"{_target_commit_sql(fence)}\n"
                "COMMIT TRANSACTION;"
            ),
            options=_claim_config(fence),
        )

    def _required_client(self) -> _FenceClient:
        if self.client is None:
            self.client = cast(
                "_FenceClient",
                bigquery.Client(project=self.project, **google_client_options()),
            )
        return self.client


def _target_fence_table_sql(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS `{table}` ("
        "target_id STRING NOT NULL, pipeline_id STRING NOT NULL, "
        "authority_id STRING NOT NULL, authority_epoch INT64 NOT NULL, "
        "run_id STRING NOT NULL, fencing_token INT64 NOT NULL, "
        "status STRING NOT NULL, claimed_at TIMESTAMP NOT NULL, committed_at TIMESTAMP) "
        "CLUSTER BY target_id, pipeline_id"
    )


def _target_claim_sql(table: str) -> str:
    return (
        f"MERGE `{table}` AS current\n"
        "USING (SELECT @dander_target_id AS target_id, "
        "@dander_pipeline_id AS pipeline_id, @dander_authority_id AS authority_id, "
        "@dander_authority_epoch AS authority_epoch, @dander_run_id AS run_id, "
        "@dander_fencing_token AS fencing_token) AS incoming\n"
        "ON current.target_id = incoming.target_id "
        "AND current.pipeline_id = incoming.pipeline_id\n"
        "WHEN MATCHED AND current.authority_id = incoming.authority_id "
        "AND current.authority_epoch = incoming.authority_epoch "
        "AND (incoming.fencing_token > current.fencing_token OR "
        "(incoming.fencing_token = current.fencing_token "
        "AND incoming.run_id = current.run_id)) THEN\n"
        "  UPDATE SET run_id = incoming.run_id, fencing_token = incoming.fencing_token, "
        "status = 'claimed', claimed_at = CURRENT_TIMESTAMP(), committed_at = NULL\n"
        "WHEN NOT MATCHED THEN\n"
        "  INSERT (target_id, pipeline_id, authority_id, authority_epoch, run_id, "
        "fencing_token, status, claimed_at, committed_at)\n"
        "  VALUES (incoming.target_id, incoming.pipeline_id, incoming.authority_id, "
        "incoming.authority_epoch, incoming.run_id, incoming.fencing_token, 'claimed', "
        "CURRENT_TIMESTAMP(), NULL)"
    )


def _target_touch_sql(fence: TargetFence) -> str:
    return (
        f"UPDATE `{fence.fence_table}` SET claimed_at = claimed_at\n"
        f"WHERE {_target_match_sql()};\n"
        "ASSERT @@row_count = 1 AS 'Dander destination fence lost';"
    )


def _target_commit_sql(fence: TargetFence) -> str:
    return (
        f"UPDATE `{fence.fence_table}`\n"
        "SET status = 'committed', committed_at = CURRENT_TIMESTAMP()\n"
        f"WHERE {_target_match_sql()};\n"
        "ASSERT @@row_count = 1 AS 'Dander destination fence lost';"
    )


def _target_match_sql() -> str:
    return (
        "target_id = @dander_target_id AND pipeline_id = @dander_pipeline_id "
        "AND authority_id = @dander_authority_id "
        "AND authority_epoch = @dander_authority_epoch "
        "AND run_id = @dander_run_id AND fencing_token = @dander_fencing_token "
        "AND status IN ('claimed', 'committed')"
    )


def _target_fence_config(target_id: str, fence: FencingToken) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("dander_target_id", "STRING", target_id),
            bigquery.ScalarQueryParameter("dander_pipeline_id", "STRING", fence.pipeline_id),
            bigquery.ScalarQueryParameter(
                "dander_authority_id", "STRING", fence.resolved_authority_id
            ),
            bigquery.ScalarQueryParameter("dander_authority_epoch", "INT64", fence.authority_epoch),
            bigquery.ScalarQueryParameter("dander_run_id", "STRING", fence.run_id),
            bigquery.ScalarQueryParameter("dander_fencing_token", "INT64", fence.token),
        ]
    )


def _claim_config(fence: TargetFence) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("dander_target_id", "STRING", fence.target_id),
            bigquery.ScalarQueryParameter("dander_pipeline_id", "STRING", fence.pipeline_id),
            bigquery.ScalarQueryParameter("dander_authority_id", "STRING", fence.authority_id),
            bigquery.ScalarQueryParameter("dander_authority_epoch", "INT64", fence.authority_epoch),
            bigquery.ScalarQueryParameter("dander_run_id", "STRING", fence.run_id),
            bigquery.ScalarQueryParameter("dander_fencing_token", "INT64", fence.token),
        ]
    )


__all__ = ["BigQueryTargetFence"]
