"""Runtime orchestration for source extraction, idempotent loading, and cursor commits."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from itertools import batched
from math import isfinite
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dander.state.run_history import RunHistoryStore, RunStatus
from dander.warehouse import RelationRef
from dander.writer.base import WriteField, WriteTarget

if TYPE_CHECKING:
    from dander.concurrency import OwnershipGuard
    from dander.ingestion.source import Endpoint, RawField, Source
    from dander.state.watermark import WatermarkStore
    from dander.telemetry import OperationTelemetry
    from dander.warehouse import WarehouseSchemaMapper, WarehouseTargetFence
    from dander.writer.base import WritePattern

_LOGGER = logging.getLogger(__name__)


class CursorValueError(ValueError):
    """Raised when a cursor-enabled endpoint returns an unusable cursor."""


class WatermarkConflictError(RuntimeError):
    """Raised when another run changed a cursor boundary before this run committed."""


@dataclass(frozen=True)
class EndpointRunResult:
    """Non-sensitive execution summary for one endpoint."""

    endpoint: str
    extracted: int
    affected: int
    committed_cursor: str | None
    telemetry: tuple[OperationTelemetry, ...] = ()


@dataclass(frozen=True)
class PipelineRunResult:
    """Execution summary for one connector run."""

    run_id: str
    source: str
    endpoints: tuple[EndpointRunResult, ...]

    @property
    def telemetry(self) -> tuple[OperationTelemetry, ...]:
        """Return endpoint operations in deterministic execution order."""
        return tuple(operation for endpoint in self.endpoints for operation in endpoint.telemetry)


class PipelineRunner:
    """Coordinate one configured source without depending on concrete providers."""

    def __init__(
        self,
        *,
        source: Source,
        writer: WritePattern,
        watermarks: WatermarkStore,
        endpoint_relations: Mapping[str, RelationRef] | None = None,
        project: str | None = None,
        dataset: str | None = None,
        resume_from_watermark: bool = True,
        history: RunHistoryStore | None = None,
        batch_rows: int = 10_000,
        endpoint_names: Iterable[str] | None = None,
        target_fence: WarehouseTargetFence | None = None,
        schema_mapper: WarehouseSchemaMapper | None = None,
    ) -> None:
        self._source = source
        self._writer = writer
        self._watermarks = watermarks
        self._resume_from_watermark = resume_from_watermark
        self._history = history
        self._target_fence = target_fence
        self._schema_mapper = schema_mapper
        if isinstance(batch_rows, bool) or not 1 <= batch_rows <= 100_000:
            raise ValueError("batch_rows must be an integer from 1 to 100000")
        self._batch_rows = batch_rows
        configured = {endpoint.name for endpoint in source.config.endpoints}
        if endpoint_relations is None:
            if project is None or dataset is None:
                raise ValueError(
                    "PipelineRunner requires endpoint_relations or legacy project/dataset"
                )
            endpoint_relations = {
                endpoint: RelationRef(
                    catalog=project,
                    namespace=dataset,
                    name=f"{source.config.name}_{endpoint}",
                )
                for endpoint in configured
            }
        elif project is not None or dataset is not None:
            raise ValueError(
                "PipelineRunner cannot combine endpoint_relations with project/dataset"
            )
        if missing := sorted(configured - endpoint_relations.keys()):
            raise ValueError(f"Missing target relation for endpoint: {missing[0]!r}")
        if unknown_relations := sorted(endpoint_relations.keys() - configured):
            raise ValueError(f"Unknown target relation endpoint: {unknown_relations[0]!r}")
        self._endpoint_relations = dict(endpoint_relations)
        if endpoint_names is None:
            self._endpoint_names = None
        else:
            requested = tuple(endpoint_names)
            if not requested:
                raise ValueError("endpoint_names must select at least one endpoint")
            if len(requested) != len(set(requested)):
                raise ValueError("endpoint_names must not contain duplicates")
            if unknown := sorted(set(requested) - configured):
                raise ValueError(f"Unknown configured endpoint: {unknown[0]!r}")
            self._endpoint_names = frozenset(requested)

    def run(
        self,
        *,
        run_id: str | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> PipelineRunResult:
        """Run every configured endpoint and commit each cursor after its successful write."""
        run_id = run_id or uuid4().hex
        source_name = self._source.config.name
        _LOGGER.info(
            "pipeline_started",
            extra={"dander_event": "pipeline_started", "run_id": run_id, "source": source_name},
        )
        if self._history is not None:
            self._history.start(run_id, source_name)
        completed: list[EndpointRunResult] = []
        try:
            for endpoint in self._source.config.endpoints:
                if self._endpoint_names is not None and endpoint.name not in self._endpoint_names:
                    continue
                completed.append(self._run_endpoint(endpoint, run_id, ownership))
        except Exception:
            try:
                self._finish_history(run_id, completed, succeeded=False)
            except Exception:
                _LOGGER.exception(
                    "run_history_finish_failed",
                    extra={
                        "dander_event": "run_history_finish_failed",
                        "run_id": run_id,
                        "source": source_name,
                    },
                )
            raise
        results = tuple(completed)
        self._finish_history(run_id, completed, succeeded=True)
        _LOGGER.info(
            "pipeline_finished",
            extra={"dander_event": "pipeline_finished", "run_id": run_id, "source": source_name},
        )
        return PipelineRunResult(run_id=run_id, source=source_name, endpoints=results)

    def _finish_history(
        self,
        run_id: str,
        completed: list[EndpointRunResult],
        *,
        succeeded: bool,
    ) -> None:
        if self._history is None:
            return
        self._history.finish(
            run_id,
            RunStatus.SUCCEEDED if succeeded else RunStatus.FAILED,
            endpoints=len(completed),
            extracted=sum(result.extracted for result in completed),
            affected=sum(result.affected for result in completed),
        )

    def _run_endpoint(
        self,
        endpoint: Endpoint,
        run_id: str,
        ownership: OwnershipGuard | None,
    ) -> EndpointRunResult:
        source_name = self._source.config.name
        legacy_schema = tuple(_write_field(field) for field in endpoint.raw_schema)
        declared_schema = endpoint.canonical_raw_schema() if endpoint.raw_schema else None
        if declared_schema is not None and self._schema_mapper is not None:
            declared_schema = self._schema_mapper.canonical_schema(declared_schema.fields)
        target = WriteTarget(
            relation=self._endpoint_relations[endpoint.name],
            business_key=tuple(endpoint.primary_key),
            schema=legacy_schema,
            declared_schema=declared_schema,
            fence=ownership.fence if ownership is not None else None,
        )
        stored_cursor = (
            self._watermarks.get(source_name, endpoint.name)
            if endpoint.incremental_cursor
            else None
        )
        cursor = stored_cursor if self._resume_from_watermark else None
        if ownership is not None:
            ownership.verify()
        if self._writer.requires_publication_fence:
            if ownership is None or ownership.fence is None or self._target_fence is None:
                raise RuntimeError("Hosted destination writes require target-fence ownership")
            target = replace(
                target,
                publication_fence=self._target_fence.claim(
                    target.relation_ref,
                    ownership.fence,
                ),
            )
        if not endpoint.raw_schema:
            _LOGGER.warning(
                "undeclared_raw_schema_deprecated",
                extra={
                    "dander_event": "undeclared_raw_schema_deprecated",
                    "endpoint": endpoint.name,
                    "source": source_name,
                },
            )
        observation = _RecordObservation(endpoint)
        records = _observed_records(
            _normalized_records(
                self._source.extract(endpoint.name, since=cursor),
                endpoint,
            ),
            observation,
        )
        if self._writer.supports_batched_writes:
            affected, telemetry = self._write_batched(
                records,
                endpoint=endpoint,
                target=target,
                run_id=run_id,
                ownership=ownership,
            )
        elif self._writer.accepts_streaming_input:
            if ownership is not None:
                ownership.verify()
            affected = self._writer.write(records, target)
            telemetry = self._writer.drain_telemetry()
        else:
            buffered = list(records)
            if ownership is not None:
                ownership.verify()
            affected = self._writer.write(buffered, target)
            telemetry = self._writer.drain_telemetry()
        committed_cursor: str | None = None
        if observation.extracted and observation.maximum_cursor is not None:
            if ownership is not None:
                ownership.verify()
            cursor_to_commit = observation.maximum_cursor
            # A deliberate read-only cursor still records proof of progress, but its source does
            # not filter by the stored boundary. Deletions must not move that watermark backward.
            if stored_cursor is not None and (
                endpoint.cursor_param == "" or not self._resume_from_watermark
            ):
                cursor_to_commit = max(stored_cursor, cursor_to_commit)
            committed = self._watermarks.compare_and_set(
                source_name,
                endpoint.name,
                expected=stored_cursor,
                cursor=cursor_to_commit,
                fence=ownership.fence if ownership is not None else None,
            )
            if not committed:
                raise WatermarkConflictError(
                    f"Watermark boundary changed for endpoint {endpoint.name!r}"
                )
            committed_cursor = cursor_to_commit

        _LOGGER.info(
            "endpoint_finished",
            extra={
                "affected": affected,
                "dander_event": "endpoint_finished",
                "endpoint": endpoint.name,
                "extracted": observation.extracted,
                "run_id": run_id,
                "source": source_name,
            },
        )
        return EndpointRunResult(
            endpoint=endpoint.name,
            extracted=observation.extracted,
            affected=affected,
            committed_cursor=committed_cursor,
            telemetry=telemetry,
        )

    def _write_batched(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        endpoint: Endpoint,
        target: WriteTarget,
        run_id: str,
        ownership: OwnershipGuard | None,
    ) -> tuple[int, tuple[OperationTelemetry, ...]]:
        affected = 0
        telemetry: list[OperationTelemetry] = []
        wrote_batch = False
        for batch_index, record_batch in enumerate(
            batched(records, self._batch_rows),
            start=1,
        ):
            wrote_batch = True
            if ownership is not None:
                ownership.verify()
            batch_affected = self._writer.write(record_batch, target)
            telemetry.extend(self._writer.drain_telemetry())
            affected += batch_affected
            _LOGGER.info(
                "batch_finished",
                extra={
                    "affected": batch_affected,
                    "batch": batch_index,
                    "dander_event": "batch_finished",
                    "endpoint": endpoint.name,
                    "extracted": len(record_batch),
                    "run_id": run_id,
                    "source": self._source.config.name,
                },
            )
        if not wrote_batch:
            if ownership is not None:
                ownership.verify()
            affected = self._writer.write((), target)
            telemetry.extend(self._writer.drain_telemetry())
        return affected, tuple(telemetry)


class _RecordObservation:
    """Track safe endpoint aggregates while records flow through bounded writes."""

    def __init__(self, endpoint: Endpoint) -> None:
        self._endpoint = endpoint
        self.extracted = 0
        self.maximum_cursor: str | None = None

    def observe(self, record: Mapping[str, Any]) -> None:
        index = self.extracted
        self.extracted += 1
        cursor_field = self._endpoint.incremental_cursor
        if cursor_field is None:
            return
        value = record.get(cursor_field)
        if value is None or isinstance(value, (dict, list)):
            raise CursorValueError(
                f"Endpoint {self._endpoint.name!r} record {index} has no scalar "
                f"cursor field {cursor_field!r}"
            )
        cursor = value.isoformat() if isinstance(value, datetime) else str(value)
        self.maximum_cursor = (
            max(self.maximum_cursor, cursor) if self.maximum_cursor is not None else cursor
        )


def _observed_records(
    records: Iterable[Mapping[str, Any]],
    observation: _RecordObservation,
) -> Iterable[Mapping[str, Any]]:
    for record in records:
        observation.observe(record)
        yield record


class RawSchemaError(ValueError):
    """Raised when a source record does not match its declared raw schema."""


def _write_field(field: RawField) -> WriteField:
    return WriteField(
        name=field.name,
        data_type=field.data_type,
        mode=field.mode,
        fields=tuple(_write_field(child) for child in field.fields),
        extensions=field.extensions,
    )


def _normalized_records(
    records: Iterable[Mapping[str, Any]],
    endpoint: Endpoint,
) -> Iterable[Mapping[str, Any]]:
    if not endpoint.raw_schema:
        yield from records
        return
    for index, record in enumerate(records):
        yield _normalize_record(record, endpoint.raw_schema, path=f"record[{index}]")


def _normalize_record(
    record: Mapping[str, Any],
    fields: list[RawField],
    *,
    path: str,
) -> dict[str, Any]:
    declared = {field.name for field in fields}
    if unknown := sorted(set(record) - declared):
        raise RawSchemaError(f"Undeclared field at {path}.{unknown[0]}")
    normalized: dict[str, Any] = {}
    for field in fields:
        field_path = f"{path}.{field.name}"
        value = record.get(field.name)
        if field.name not in record or value is None:
            if field.mode == "REQUIRED":
                raise RawSchemaError(f"Required field is missing or null at {field_path}")
            normalized[field.name] = [] if field.mode == "REPEATED" else None
            continue
        normalized[field.name] = _normalize_field(value, field, path=field_path)
    return normalized


def _normalize_field(value: object, field: RawField, *, path: str) -> object:
    if field.mode == "REPEATED":
        if not isinstance(value, list):
            raise RawSchemaError(f"Repeated field must be a list at {path}")
        item_field = field.model_copy(update={"mode": "NULLABLE"})
        normalized: list[object] = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            if item is None:
                raise RawSchemaError(f"Repeated field item is null at {item_path}")
            normalized.append(_normalize_field(item, item_field, path=item_path))
        return normalized
    if field.data_type == "RECORD":
        if not isinstance(value, Mapping):
            raise RawSchemaError(f"Record field must be an object at {path}")
        return _normalize_record(value, field.fields, path=path)
    if field.data_type == "JSON":
        _validate_json_value(value, path=path)
        return value
    if isinstance(value, (Mapping, list)):
        raise RawSchemaError(f"Scalar field has a structured value at {path}")
    return _normalize_scalar(value, data_type=field.data_type, path=path)


def _normalize_scalar(value: object, *, data_type: str, path: str) -> object:
    try:
        if data_type in {"STRING", "BYTES", "GEOGRAPHY", "INTERVAL"}:
            if not isinstance(value, str):
                raise TypeError
            return value
        if data_type == "BOOL":
            if not isinstance(value, bool):
                raise TypeError
            return value
        if data_type == "INT64":
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise TypeError
            normalized_int = int(value)
            if not -(2**63) <= normalized_int < 2**63:
                raise ValueError
            return normalized_int
        if data_type == "FLOAT64":
            if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
                raise TypeError
            normalized_float = float(value)
            if not isfinite(normalized_float):
                raise ValueError
            return normalized_float
        if data_type in {"NUMERIC", "BIGNUMERIC"}:
            if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
                raise TypeError
            normalized_decimal = Decimal(str(value))
            if not normalized_decimal.is_finite():
                raise ValueError
            return normalized_decimal
        if data_type == "DATE":
            if isinstance(value, datetime) or not isinstance(value, (str, date)):
                raise TypeError
            if isinstance(value, str):
                date.fromisoformat(value)
            return value
        if data_type == "DATETIME":
            parsed_datetime = datetime.fromisoformat(value) if isinstance(value, str) else value
            if not isinstance(parsed_datetime, datetime) or parsed_datetime.tzinfo is not None:
                raise TypeError
            return value
        if data_type == "TIME":
            parsed_time = time.fromisoformat(value) if isinstance(value, str) else value
            if not isinstance(parsed_time, time) or parsed_time.tzinfo is not None:
                raise TypeError
            return value
        if data_type == "TIMESTAMP":
            parsed_timestamp = (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if isinstance(value, str)
                else value
            )
            if not isinstance(parsed_timestamp, datetime) or parsed_timestamp.tzinfo is None:
                raise TypeError
            return parsed_timestamp
    except (InvalidOperation, OverflowError, TypeError, ValueError):
        raise RawSchemaError(f"Invalid {data_type} field at {path}") from None
    raise RawSchemaError(f"Unsupported declared field type at {path}")


def _validate_json_value(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise RawSchemaError(f"Invalid JSON field at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RawSchemaError(f"Invalid JSON object key at {path}")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise RawSchemaError(f"Invalid JSON field at {path}")
