"""Metadata-only BigQuery input-size estimation for the supported graph runtime."""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from dander.control.input_size_estimator import InputSizeEstimate, InputSizeEstimationError
from dander.identity.aws_google import prepare_fargate_google_identity
from dander.pipeline.node_config import SourceNodeConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from dander.control.graph_store import GraphRecord

_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,1023}$")
_MAX_BOUNDED_INTEGER = 9_223_372_036_854_775_807
_API_ROOT = "https://bigquery.googleapis.com/bigquery/v2"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class _Response(Protocol):
    status_code: int

    def json(self) -> object: ...


class _Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> _Response: ...

    def close(self) -> None: ...


class BigQueryInputSizeEstimator:
    """Sum current raw-table logical bytes using BigQuery table metadata only."""

    def __init__(
        self,
        project_id: str,
        raw_dataset: str,
        *,
        transport: _Transport | None = None,
        credential_factory: Callable[[], object] = prepare_fargate_google_identity,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if _PROJECT.fullmatch(project_id) is None or _IDENTIFIER.fullmatch(raw_dataset) is None:
            raise ValueError("BigQuery estimator coordinates are invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("BigQuery estimator timeout is invalid.")
        self._project_id = project_id
        self._raw_dataset = raw_dataset
        self._transport = transport
        self._credential_factory = credential_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = float(timeout_seconds)

    def estimate(self, record: GraphRecord) -> InputSizeEstimate:
        """Read bounded metadata for every unique source relation in the graph."""
        tables = self._source_tables(record)
        transport = self._transport
        owns_transport = transport is None
        if transport is None:
            try:
                from google.auth.transport.requests import AuthorizedSession

                transport = cast(
                    "_Transport",
                    AuthorizedSession(self._credential_factory()),  # type: ignore[no-untyped-call]
                )
            except Exception as error:  # noqa: BLE001 - normalize identity and session failures
                raise InputSizeEstimationError(
                    "BigQuery metadata identity is unavailable."
                ) from error
        try:
            total = 0
            for table in tables:
                response = transport.request(
                    "GET",
                    f"{_API_ROOT}/projects/{self._project_id}/datasets/"
                    f"{self._raw_dataset}/tables/{table}",
                    params={"view": "STORAGE_STATS"},
                    timeout=self._timeout,
                )
                if response.status_code != 200:
                    raise InputSizeEstimationError("BigQuery table metadata is unavailable.")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise InputSizeEstimationError("BigQuery table metadata is invalid.")
                raw_bytes = payload.get("numBytes")
                if (
                    not isinstance(raw_bytes, str)
                    or not raw_bytes.isascii()
                    or not raw_bytes.isdigit()
                ):
                    raise InputSizeEstimationError("BigQuery table metadata is invalid.")
                table_bytes = int(raw_bytes)
                if table_bytes > _MAX_BOUNDED_INTEGER - total:
                    raise InputSizeEstimationError("BigQuery input size exceeds its bound.")
                total += table_bytes
            return InputSizeEstimate(
                estimated_input_bytes=total,
                source="bigquery_table_metadata",
                observed_at=self._clock(),
            )
        except InputSizeEstimationError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize provider transport failures
            raise InputSizeEstimationError("BigQuery table metadata is unavailable.") from error
        finally:
            if owns_transport:
                with suppress(Exception):  # noqa: BLE001 - metadata result is already normalized
                    transport.close()

    @staticmethod
    def _source_tables(record: GraphRecord) -> tuple[str, ...]:
        tables: set[str] = set()
        for node in record.document.to_domain().nodes:
            if node.type != "source":
                continue
            config = node.config
            if not isinstance(config, SourceNodeConfig):
                raise InputSizeEstimationError("Graph source metadata is invalid.")
            connector = config.connector
            endpoint = config.endpoint
            if connector is None or endpoint is None:
                raise InputSizeEstimationError("Graph source metadata is incomplete.")
            table = f"{connector}_{endpoint}"
            if _IDENTIFIER.fullmatch(table) is None:
                raise InputSizeEstimationError("Graph source metadata is invalid.")
            tables.add(table)
        if not tables or len(tables) > 100:
            raise InputSizeEstimationError("Graph source metadata is incomplete.")
        return tuple(sorted(tables))


__all__ = ["BigQueryInputSizeEstimator"]
