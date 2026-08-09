"""BigQuery durable-state runtime and explicit schema migrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from google.cloud import bigquery

from dander.catalog import BigQueryMetadataStore
from dander.identity import google_client_options
from dander.providers.bigquery.config import BigQueryStateConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.state import (
    BigQueryLeaseStore,
    BigQueryRunHistoryStore,
    BigQueryWatermarkStore,
    StateCapabilities,
    StateMigration,
    StateRuntime,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import BaseModel

_STATE_SCHEMA_VERSION = 1
_MIGRATIONS = (StateMigration(version=1, name="existing_control_tables"),)


class _QueryJob(Protocol):
    def result(self) -> Iterable[Mapping[str, Any]]:
        """Wait for query completion and return projected rows."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        """Execute one Standard SQL statement."""


class BigQueryStateMigrator:
    """Version existing BigQuery control tables without changing their identities."""

    def __init__(
        self,
        *,
        project: str,
        dataset: str,
        client: _BigQueryClient,
        watermarks: BigQueryWatermarkStore,
        history: BigQueryRunHistoryStore,
        metadata: BigQueryMetadataStore | None,
    ) -> None:
        self._table = f"{project}.{dataset}._dander_state_schema"
        self._client = client
        self._watermarks = watermarks
        self._history = history
        self._metadata = metadata
        self._ledger_ready = False

    @property
    def migrations(self) -> tuple[StateMigration, ...]:
        return _MIGRATIONS

    def current_version(self) -> int:
        """Return the latest recorded migration, creating only the ledger if absent."""
        self._ensure_ledger()
        rows = list(
            self._client.query(
                f"SELECT COALESCE(MAX(version), 0) AS version FROM `{self._table}`"
            ).result()
        )
        if len(rows) != 1:
            raise RuntimeError("BigQuery state migration ledger returned an invalid result")
        version = rows[0]["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise RuntimeError("BigQuery state migration ledger contains an invalid version")
        if version > _STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"BigQuery state schema version {version} is newer than this Dander runtime"
            )
        return version

    def migrate(self) -> int:
        """Apply outstanding idempotent migrations and record only completed work."""
        version = self.current_version()
        if version >= _STATE_SCHEMA_VERSION:
            return version

        self._watermarks.migrate()
        self._history.migrate()
        if self._metadata is not None:
            self._metadata.migrate()
        self._record(_MIGRATIONS[0])
        return _STATE_SCHEMA_VERSION

    def _ensure_ledger(self) -> None:
        if self._ledger_ready:
            return
        self._client.query(
            f"CREATE TABLE IF NOT EXISTS `{self._table}` ("
            "version INT64 NOT NULL, name STRING NOT NULL, applied_at TIMESTAMP NOT NULL) "
            "CLUSTER BY version"
        ).result()
        self._ledger_ready = True

    def _record(self, migration: StateMigration) -> None:
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("version", "INT64", migration.version),
                bigquery.ScalarQueryParameter("name", "STRING", migration.name),
            ]
        )
        self._client.query(
            f"MERGE `{self._table}` AS target "
            "USING (SELECT @version AS version, @name AS name) AS incoming "
            "ON target.version = incoming.version "
            "WHEN NOT MATCHED THEN INSERT (version, name, applied_at) "
            "VALUES (incoming.version, incoming.name, CURRENT_TIMESTAMP())",
            job_config=config,
        ).result()


def _build_bigquery_state(
    config: BaseModel,
    context: Mapping[str, object],
) -> StateRuntime:
    if not isinstance(config, BigQueryStateConfig):
        raise TypeError("BigQuery state factory received the wrong configuration")
    project = _required_text(context, "catalog", legacy="project")
    raw_dataset = _required_text(context, "raw_namespace", legacy="raw_dataset")
    metadata_dataset = _required_text(
        context,
        "metadata_namespace",
        legacy="metadata_dataset",
    )
    project_pipeline = _required_bool(context, "project_pipeline")
    metadata_enabled = _required_bool(context, "metadata_enabled")
    control_dataset = metadata_dataset if project_pipeline else raw_dataset
    supplied_client = context.get("client")
    client: Any = (
        supplied_client
        if supplied_client is not None
        else bigquery.Client(project=project, **google_client_options())
    )

    watermarks = BigQueryWatermarkStore(project=project, dataset=raw_dataset, client=client)
    history = BigQueryRunHistoryStore(project=project, dataset=control_dataset, client=client)
    leases = BigQueryLeaseStore(project=project, dataset=control_dataset, client=client)
    metadata = (
        BigQueryMetadataStore(project=project, dataset=metadata_dataset, client=client)
        if metadata_enabled
        else None
    )
    migrator = BigQueryStateMigrator(
        project=project,
        dataset=control_dataset,
        client=client,
        watermarks=watermarks,
        history=history,
        metadata=metadata,
    )
    return StateRuntime(
        provider_id="bigquery",
        leases=leases,
        watermarks=watermarks,
        history=history,
        metadata=metadata,
        migrator=migrator,
        capabilities=StateCapabilities(
            provider_id="bigquery",
            schema_version=_STATE_SCHEMA_VERSION,
            server_time=True,
            atomic_leases=True,
            monotonic_fencing=True,
            atomic_watermark_cas=True,
            interrupted_run_reconciliation=True,
        ),
    )


def _required_text(
    context: Mapping[str, object],
    name: str,
    *,
    legacy: str | None = None,
) -> str:
    value = context.get(name, context.get(legacy) if legacy is not None else None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"BigQuery state runtime requires a non-empty {name}")
    return value


def _required_bool(context: Mapping[str, object], name: str) -> bool:
    value = context.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"BigQuery state runtime requires a boolean {name}")
    return value


BIGQUERY_STATE_FACTORY = ProviderFactory[StateRuntime](
    kind=ProviderKind.STATE,
    provider_id="bigquery",
    api_version=PROVIDER_API_VERSION,
    build=_build_bigquery_state,
)

__all__ = ["BIGQUERY_STATE_FACTORY", "BigQueryStateMigrator"]
