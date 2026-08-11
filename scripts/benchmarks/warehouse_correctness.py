#!/usr/bin/env python3
"""Run and compare the bounded four-warehouse correctness fixture."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers import ProviderKind, default_provider_registry
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    RelationRef,
    RelationSchema,
    WarehouseRuntime,
    normalize_staging_record,
)
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from typing import Any, Protocol

    from dander.providers.postgresql.fence import PostgreSQLTargetFence
    from dander.providers.redshift.fence import RedshiftTargetFence
    from dander.providers.redshift.runtime import RedshiftWriterFactory
    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.telemetry import OperationTelemetry

    class _S3Inspector(Protocol):
        def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

        def delete_objects(self, **kwargs: object) -> Mapping[str, object]: ...


_EVIDENCE_SCHEMA = "io.dander.conformance.warehouse-correctness/v1"
_FAILURE_EVIDENCE_SCHEMA = "io.dander.conformance.warehouse-correctness-failure/v1"
_COMPARISON_SCHEMA = "io.dander.conformance.warehouse-correctness-comparison/v1"
_FIXTURE_VERSION = "warehouse-common-scalar-v1"
_PROVIDERS = frozenset({"bigquery", "postgresql", "snowflake", "redshift"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{2,255}$")
_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_TARGET_PREFIX = "dander_conform_"
_BIGQUERY_MAXIMUM_BYTES_BILLED = 10 * 1_024 * 1_024
_FAILURE_STAGES = frozenset(
    {
        "open_session",
        "schema_contract",
        "fence",
        "initial_write",
        "update_write",
        "readback",
        "replay_write",
        "replay_readback",
        "transport",
        "cleanup",
        "cleanup_verification",
        "close_session",
    }
)


class WarehouseCorrectnessError(RuntimeError):
    """Raised with a sanitized four-warehouse conformance failure."""


class _WarehouseCorrectnessRunError(WarehouseCorrectnessError):
    """Carry one sanitized failed-run record without provider messages or payloads."""

    def __init__(self, evidence: WarehouseCorrectnessFailureEvidence) -> None:
        super().__init__(f"{evidence.provider} warehouse correctness run failed")
        self.evidence = evidence


COMMON_SCHEMA = RelationSchema(
    fields=(
        CanonicalField(
            name="id",
            data_type=CanonicalType(kind=LogicalTypeKind.STRING),
            cardinality=FieldCardinality.REQUIRED,
        ),
        CanonicalField(
            name="active",
            data_type=CanonicalType(kind=LogicalTypeKind.BOOLEAN),
            cardinality=FieldCardinality.REQUIRED,
        ),
        CanonicalField(
            name="units",
            data_type=CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
        ),
        CanonicalField(
            name="amount",
            data_type=CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=38, scale=9),
        ),
        CanonicalField(
            name="ratio",
            data_type=CanonicalType(kind=LogicalTypeKind.FLOAT, bit_width=64),
        ),
        CanonicalField(name="label", data_type=CanonicalType(kind=LogicalTypeKind.STRING)),
        CanonicalField(name="payload", data_type=CanonicalType(kind=LogicalTypeKind.BINARY)),
        CanonicalField(name="event_date", data_type=CanonicalType(kind=LogicalTypeKind.DATE)),
        CanonicalField(
            name="event_time",
            data_type=CanonicalType(
                kind=LogicalTypeKind.TIME,
                fractional_second_precision=6,
            ),
        ),
        CanonicalField(
            name="observed_at",
            data_type=CanonicalType(
                kind=LogicalTypeKind.TIMESTAMP,
                with_timezone=True,
                fractional_second_precision=6,
            ),
        ),
        CanonicalField(
            name="local_at",
            data_type=CanonicalType(
                kind=LogicalTypeKind.TIMESTAMP,
                with_timezone=False,
                fractional_second_precision=6,
            ),
        ),
    )
)

LEGACY_BIGQUERY_FIELDS = (
    WriteField(name="id", data_type="STRING", mode="REQUIRED"),
    WriteField(name="active", data_type="BOOLEAN", mode="REQUIRED"),
    WriteField(name="units", data_type="INT64"),
    WriteField(name="amount", data_type="NUMERIC"),
    WriteField(name="ratio", data_type="FLOAT64"),
    WriteField(name="label", data_type="STRING"),
    WriteField(name="payload", data_type="BYTES"),
    WriteField(name="event_date", data_type="DATE"),
    WriteField(name="event_time", data_type="TIME"),
    WriteField(name="observed_at", data_type="TIMESTAMP"),
    WriteField(name="local_at", data_type="DATETIME"),
)

_INITIAL_ROWS: tuple[dict[str, object], ...] = (
    {
        "id": "alpha",
        "active": True,
        "units": 1,
        "amount": Decimal("10.250000000"),
        "ratio": 0.5,
        "label": "older",
        "payload": b"\x00\x7f",
        "event_date": date(2026, 1, 2),
        "event_time": time(3, 4, 5, 123456),
        "observed_at": datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "local_at": datetime(2026, 1, 2, 3, 4, 5, 123456),
    },
    {
        "id": "beta",
        "active": False,
        "units": -2,
        "amount": Decimal("0.000000001"),
        "ratio": -2.0,
        "label": None,
        "payload": b"dander",
        "event_date": date(2026, 2, 3),
        "event_time": time(6, 7, 8),
        "observed_at": datetime(2026, 2, 3, 6, 7, 8, tzinfo=UTC),
        "local_at": datetime(2026, 2, 3, 6, 7, 8),
    },
    {
        "id": "alpha",
        "active": True,
        "units": 2,
        "amount": Decimal("11.250000000"),
        "ratio": 0.25,
        "label": "newer",
        "payload": b"\x00\x7f",
        "event_date": date(2026, 1, 2),
        "event_time": time(3, 4, 5, 123456),
        "observed_at": datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "local_at": datetime(2026, 1, 2, 3, 4, 5, 123456),
    },
)

_UPDATE_ROWS: tuple[dict[str, object], ...] = (
    {
        "id": "alpha",
        "active": False,
        "units": 3,
        "amount": Decimal("12.500000000"),
        "ratio": 0.5,
        "label": "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "payload": b"updated",
        "event_date": date(2026, 3, 4),
        "event_time": time(9, 10, 11, 654321),
        "observed_at": datetime(2026, 3, 4, 9, 10, 11, 654321, tzinfo=UTC),
        "local_at": datetime(2026, 3, 4, 9, 10, 11, 654321),
    },
    {
        "id": "gamma",
        "active": True,
        "units": 0,
        "amount": Decimal("999.000000000"),
        "ratio": 0.0,
        "label": "third",
        "payload": b"\xff",
        "event_date": date(2026, 4, 5),
        "event_time": time(12, 13, 14),
        "observed_at": datetime(2026, 4, 5, 12, 13, 14, tzinfo=UTC),
        "local_at": datetime(2026, 4, 5, 12, 13, 14),
    },
)

_EXPECTED_ROWS = (_UPDATE_ROWS[0], _INITIAL_ROWS[1], _UPDATE_ROWS[1])


@dataclass(frozen=True, slots=True)
class ApprovedCostCeiling:
    """Human-reviewed maximum for one bounded live provider invocation."""

    usd: str
    approval_reference: str

    def __post_init__(self) -> None:
        try:
            amount = Decimal(self.usd)
        except InvalidOperation as error:
            raise ValueError("approved cost ceiling must be a decimal USD amount") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError("approved cost ceiling must be finite and nonnegative")
        if not _APPROVAL_REFERENCE.fullmatch(self.approval_reference):
            raise ValueError("approval reference must be a stable non-secret identifier")
        object.__setattr__(self, "usd", format(amount, "f"))


@dataclass(frozen=True, slots=True)
class WarehouseCorrectnessEvidence:
    """Sanitized proof for one provider; normalized rows are represented only by hashes."""

    schema: str
    fixture_version: str
    provider: str
    candidate_commit: str
    dander_version: str
    started_at_utc: str
    ended_at_utc: str
    fixture_hash: str
    canonical_schema_hash: str
    normalized_rows_hash: str
    normalized_row_count: int
    write_mode: str
    transport: str
    replay_equal: bool
    cleanup_verified: bool
    approved_cost_ceiling_usd: str
    cost_approval_reference: str
    status: str = "passed"

    def __post_init__(self) -> None:
        if self.schema != _EVIDENCE_SCHEMA or self.fixture_version != _FIXTURE_VERSION:
            raise ValueError("warehouse correctness evidence uses an unknown schema")
        if self.provider not in _PROVIDERS:
            raise ValueError("warehouse correctness evidence names an unknown provider")
        if not _COMMIT.fullmatch(self.candidate_commit):
            raise ValueError("warehouse correctness evidence requires a full commit SHA")
        for value in (
            self.fixture_hash,
            self.canonical_schema_hash,
            self.normalized_rows_hash,
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError("warehouse correctness evidence contains an invalid hash")
        if self.normalized_row_count < 0:
            raise ValueError("warehouse correctness row count must be nonnegative")
        if self.write_mode != WriteMode.SCD1.value:
            raise ValueError("warehouse correctness evidence must use SCD1")
        if not self.replay_equal or not self.cleanup_verified or self.status != "passed":
            raise ValueError("warehouse correctness evidence is not a passing proof")
        ApprovedCostCeiling(
            self.approved_cost_ceiling_usd,
            self.cost_approval_reference,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> WarehouseCorrectnessEvidence:
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != expected:
            raise ValueError("warehouse correctness evidence has an invalid shape")
        return cls(**cast("Any", dict(value)))


@dataclass(frozen=True, slots=True)
class WarehouseCorrectnessFailureEvidence:
    """Sanitized failed-run record containing no provider messages, SQL, credentials, or rows."""

    schema: str
    fixture_version: str
    provider: str
    candidate_commit: str
    dander_version: str
    started_at_utc: str
    ended_at_utc: str
    stage: str
    primary_error_types: tuple[str, ...]
    cleanup_error_types: tuple[str, ...]
    cleanup_attempted: bool
    cleanup_verified: bool
    approved_cost_ceiling_usd: str
    cost_approval_reference: str
    status: str = "failed"

    def __post_init__(self) -> None:
        if self.schema != _FAILURE_EVIDENCE_SCHEMA or self.fixture_version != _FIXTURE_VERSION:
            raise ValueError("warehouse correctness failure evidence uses an unknown schema")
        if self.provider not in _PROVIDERS:
            raise ValueError("warehouse correctness failure evidence names an unknown provider")
        if not _COMMIT.fullmatch(self.candidate_commit):
            raise ValueError("warehouse correctness failure evidence requires a full commit SHA")
        if self.stage not in _FAILURE_STAGES:
            raise ValueError("warehouse correctness failure evidence has an unknown stage")
        error_types = (*self.primary_error_types, *self.cleanup_error_types)
        if not error_types or any(not _ERROR_TYPE.fullmatch(value) for value in error_types):
            raise ValueError("warehouse correctness failure evidence has invalid error types")
        if self.cleanup_verified and not self.cleanup_attempted:
            raise ValueError("warehouse correctness cleanup cannot pass when it was not attempted")
        if self.status != "failed":
            raise ValueError("warehouse correctness failure evidence must be failed")
        ApprovedCostCeiling(
            self.approved_cost_ceiling_usd,
            self.cost_approval_reference,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class WarehouseCorrectnessComparison:
    """Sanitized equality gate across exactly four provider evidence records."""

    schema: str
    fixture_version: str
    candidate_commit: str
    providers: tuple[str, ...]
    fixture_hash: str
    canonical_schema_hash: str
    normalized_rows_hash: str
    normalized_row_count: int
    all_rows_equal: bool
    all_cleanup_verified: bool
    status: str = "passed"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class _WarehouseSession:
    provider: str
    runtime: WarehouseRuntime
    relation: RelationRef
    schema_inputs: Sequence[object]
    transport: WriteTransport
    read_rows: Callable[[], Sequence[Mapping[str, object]]]
    cleanup: Callable[[], None]
    cleanup_verified: Callable[[], bool]
    close: Callable[[], None]


def normalized_rows(
    rows: Sequence[Mapping[str, object]],
    schema: RelationSchema = COMMON_SCHEMA,
) -> tuple[dict[str, object], ...]:
    """Normalize provider readback by canonical field semantics and stable key order."""
    normalized = [
        _normalize_row(normalize_staging_record(row, schema, row_index=index), schema)
        for index, row in enumerate(rows)
    ]
    normalized.sort(key=lambda row: cast("str", row["id"]))
    return tuple(normalized)


def compare_evidence(
    evidence: Sequence[WarehouseCorrectnessEvidence],
) -> WarehouseCorrectnessComparison:
    """Require one passing, same-candidate proof per warehouse and exact hash equality."""
    providers = [item.provider for item in evidence]
    if len(evidence) != len(_PROVIDERS) or set(providers) != _PROVIDERS:
        raise WarehouseCorrectnessError(
            "comparison requires one unique BigQuery, PostgreSQL, Snowflake, and Redshift proof"
        )
    attributes = (
        "candidate_commit",
        "fixture_hash",
        "canonical_schema_hash",
        "normalized_rows_hash",
        "normalized_row_count",
    )
    for attribute in attributes:
        if len({getattr(item, attribute) for item in evidence}) != 1:
            raise WarehouseCorrectnessError(
                f"warehouse correctness comparison found unequal {attribute}"
            )
    first = evidence[0]
    return WarehouseCorrectnessComparison(
        schema=_COMPARISON_SCHEMA,
        fixture_version=_FIXTURE_VERSION,
        candidate_commit=first.candidate_commit,
        providers=tuple(sorted(providers)),
        fixture_hash=first.fixture_hash,
        canonical_schema_hash=first.canonical_schema_hash,
        normalized_rows_hash=first.normalized_rows_hash,
        normalized_row_count=first.normalized_row_count,
        all_rows_equal=True,
        all_cleanup_verified=all(item.cleanup_verified for item in evidence),
    )


def run_provider(
    profile: Mapping[str, object],
    *,
    candidate_commit: str,
    cost_ceiling: ApprovedCostCeiling,
) -> WarehouseCorrectnessEvidence:
    """Execute the common fixture through one selected live warehouse runtime."""
    if not _COMMIT.fullmatch(candidate_commit):
        raise ValueError("candidate_commit must be a full lowercase commit SHA")
    provider = profile.get("provider")
    if provider not in _PROVIDERS:
        raise ValueError("profile must select one of the four conformance warehouses")
    started = _now()
    try:
        session = _open_session(profile)
    except Exception as error:
        failure = _failure_evidence(
            provider=provider,
            candidate_commit=candidate_commit,
            cost_ceiling=cost_ceiling,
            started_at_utc=started,
            stage="open_session",
            primary_errors=(error,),
            cleanup_errors=(),
            cleanup_attempted=False,
            cleanup_verified=False,
        )
        raise _WarehouseCorrectnessRunError(failure) from error

    evidence: WarehouseCorrectnessEvidence | None = None
    run_failure: _WarehouseCorrectnessRunError | None = None
    try:
        evidence = _run_session(
            session,
            candidate_commit=candidate_commit,
            cost_ceiling=cost_ceiling,
            started_at_utc=started,
        )
    except _WarehouseCorrectnessRunError as error:
        run_failure = error
    try:
        session.close()
    except Exception as error:
        if run_failure is None:
            failure = _failure_evidence(
                provider=provider,
                candidate_commit=candidate_commit,
                cost_ceiling=cost_ceiling,
                started_at_utc=started,
                stage="close_session",
                primary_errors=(error,),
                cleanup_errors=(),
                cleanup_attempted=evidence is not None,
                cleanup_verified=evidence is not None and evidence.cleanup_verified,
            )
        else:
            failure = replace(
                run_failure.evidence,
                cleanup_error_types=_merge_error_types(
                    run_failure.evidence.cleanup_error_types,
                    _exception_types((error,)),
                ),
                ended_at_utc=_now(),
            )
        raise _WarehouseCorrectnessRunError(failure) from error
    if run_failure is not None:
        raise run_failure
    assert evidence is not None
    return evidence


def _run_session(
    session: _WarehouseSession,
    *,
    candidate_commit: str,
    cost_ceiling: ApprovedCostCeiling,
    started_at_utc: str,
) -> WarehouseCorrectnessEvidence:
    runtime = session.runtime
    operations: list[OperationTelemetry] = []
    cleanup_error: Exception | None = None
    primary_error: Exception | None = None
    primary_stage = "schema_contract"
    before_replay: tuple[dict[str, object], ...] = ()
    after_replay: tuple[dict[str, object], ...] = ()
    try:
        runtime.capabilities.schema_support.require(COMMON_SCHEMA)
        mapped = runtime.schema_mapper.canonical_schema(session.schema_inputs)
        if _without_extensions(mapped) != COMMON_SCHEMA:
            raise WarehouseCorrectnessError(
                f"{session.provider} schema mapper changed the common canonical fixture"
            )
        writer = runtime.writers.build_ingestion_writer(
            sandbox=False,
            batch_rows=len(_INITIAL_ROWS),
            schema_evolution=SchemaEvolution.STRICT,
            mode=WriteMode.SCD1,
        )
        primary_stage = "fence"
        publication = None
        if writer.requires_publication_fence:
            publication = runtime.target_fence.claim(
                session.relation,
                FencingToken(
                    lease_table=None,
                    pipeline_id="warehouse_correctness",
                    run_id="fixture-v1",
                    token=1,
                    authority_id=f"{session.provider}:warehouse-correctness",
                ),
            )
        target = WriteTarget(
            relation=session.relation,
            business_key=("id",),
            schema=LEGACY_BIGQUERY_FIELDS,
            declared_schema=COMMON_SCHEMA,
            publication_fence=publication,
        )
        primary_stage = "initial_write"
        writer.write(_INITIAL_ROWS, target)
        operations.extend(writer.drain_telemetry())
        primary_stage = "update_write"
        writer.write(_UPDATE_ROWS, target)
        operations.extend(writer.drain_telemetry())
        primary_stage = "readback"
        before_replay = normalized_rows(session.read_rows())
        primary_stage = "replay_write"
        writer.write(_UPDATE_ROWS, target)
        operations.extend(writer.drain_telemetry())
        primary_stage = "replay_readback"
        after_replay = normalized_rows(session.read_rows())
        expected = normalized_rows(_EXPECTED_ROWS)
        if before_replay != expected or after_replay != expected:
            raise WarehouseCorrectnessError(
                f"{session.provider} normalized rows differ from the common fixture"
            )
        primary_stage = "transport"
        _require_transport(session, operations)
    except Exception as error:
        primary_error = error
    finally:
        try:
            session.cleanup()
        except Exception as error:
            cleanup_error = error

    cleaned = False
    if cleanup_error is None:
        try:
            cleaned = session.cleanup_verified()
        except Exception as error:
            cleanup_error = error
    if primary_error is not None or cleanup_error is not None:
        primary_errors = (primary_error,) if primary_error is not None else ()
        cleanup_errors = (cleanup_error,) if cleanup_error is not None else ()
        stage = primary_stage if primary_error is not None else "cleanup"
        failure = _failure_evidence(
            provider=session.provider,
            candidate_commit=candidate_commit,
            cost_ceiling=cost_ceiling,
            started_at_utc=started_at_utc,
            stage=stage,
            primary_errors=primary_errors,
            cleanup_errors=cleanup_errors,
            cleanup_attempted=True,
            cleanup_verified=cleaned,
        )
        errors = [*primary_errors, *cleanup_errors]
        raise _WarehouseCorrectnessRunError(failure) from ExceptionGroup(
            "warehouse correctness failures", errors
        )
    if not cleaned:
        failure = _failure_evidence(
            provider=session.provider,
            candidate_commit=candidate_commit,
            cost_ceiling=cost_ceiling,
            started_at_utc=started_at_utc,
            stage="cleanup_verification",
            primary_errors=(WarehouseCorrectnessError("cleanup verification returned false"),),
            cleanup_errors=(),
            cleanup_attempted=True,
            cleanup_verified=False,
        )
        raise _WarehouseCorrectnessRunError(failure)

    fixture_hash = _fixture_hash()
    schema_hash = _hash_json(COMMON_SCHEMA.model_dump(mode="json", by_alias=True))
    result_hash = _hash_json(after_replay)
    return WarehouseCorrectnessEvidence(
        schema=_EVIDENCE_SCHEMA,
        fixture_version=_FIXTURE_VERSION,
        provider=session.provider,
        candidate_commit=candidate_commit,
        dander_version=__version__,
        started_at_utc=started_at_utc,
        ended_at_utc=_now(),
        fixture_hash=fixture_hash,
        canonical_schema_hash=schema_hash,
        normalized_rows_hash=result_hash,
        normalized_row_count=len(after_replay),
        write_mode=WriteMode.SCD1.value,
        transport=session.transport.value,
        replay_equal=before_replay == after_replay,
        cleanup_verified=cleaned,
        approved_cost_ceiling_usd=cost_ceiling.usd,
        cost_approval_reference=cost_ceiling.approval_reference,
    )


def _failure_evidence(
    *,
    provider: str,
    candidate_commit: str,
    cost_ceiling: ApprovedCostCeiling,
    started_at_utc: str,
    stage: str,
    primary_errors: Sequence[BaseException],
    cleanup_errors: Sequence[BaseException],
    cleanup_attempted: bool,
    cleanup_verified: bool,
) -> WarehouseCorrectnessFailureEvidence:
    return WarehouseCorrectnessFailureEvidence(
        schema=_FAILURE_EVIDENCE_SCHEMA,
        fixture_version=_FIXTURE_VERSION,
        provider=provider,
        candidate_commit=candidate_commit,
        dander_version=__version__,
        started_at_utc=started_at_utc,
        ended_at_utc=_now(),
        stage=stage,
        primary_error_types=_exception_types(primary_errors),
        cleanup_error_types=_exception_types(cleanup_errors),
        cleanup_attempted=cleanup_attempted,
        cleanup_verified=cleanup_verified,
        approved_cost_ceiling_usd=cost_ceiling.usd,
        cost_approval_reference=cost_ceiling.approval_reference,
    )


def _exception_types(errors: Sequence[BaseException]) -> tuple[str, ...]:
    names: set[str] = set()
    pending = list(errors)
    seen: set[int] = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        name = type(error).__name__
        if name not in {"ExceptionGroup", "_WarehouseCorrectnessRunError"}:
            names.add(name if _ERROR_TYPE.fullmatch(name) else "Exception")
        if isinstance(error, BaseExceptionGroup):
            pending.extend(error.exceptions)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        elif error.__context__ is not None:
            pending.append(error.__context__)
    return tuple(sorted(names))


def _merge_error_types(*groups: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({value for group in groups for value in group}))


def _open_session(profile: Mapping[str, object]) -> _WarehouseSession:
    provider = profile["provider"]
    if provider == "bigquery":
        return _open_bigquery(profile)
    if provider == "postgresql":
        return _open_postgresql(profile)
    if provider == "snowflake":
        return _open_snowflake(profile)
    if provider == "redshift":
        return _open_redshift(profile)
    raise AssertionError("provider validation is exhaustive")


def _open_bigquery(profile: Mapping[str, object]) -> _WarehouseSession:
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery

    values = dict(profile)
    project = values.pop("project", None)
    if not isinstance(project, str) or not project:
        raise ValueError("BigQuery conformance profile requires project")
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, values)
    dataset = getattr(config, "dataset", None)
    if not isinstance(dataset, str) or not dataset:
        raise ValueError("BigQuery conformance profile requires an existing dataset")
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"catalog": project},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("BigQuery provider returned an invalid warehouse runtime")
    client = bigquery.Client(project=project)
    relation = RelationRef(
        catalog=project,
        namespace=dataset,
        name=f"{_TARGET_PREFIX}{uuid.uuid4().hex[:12]}",
    )
    table_id = ".".join(relation.coordinates)
    stage_prefix = f"_dander_stage_{relation.name}_"

    def read_rows() -> Sequence[Mapping[str, object]]:
        fields = ", ".join(f"`{field.name}`" for field in COMMON_SCHEMA.fields)
        query = f"SELECT {fields} FROM `{table_id}` ORDER BY `id`"
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=_BIGQUERY_MAXIMUM_BYTES_BILLED,
            labels={"dander-proof": "warehouse-correctness"},
        )
        return tuple(dict(row) for row in client.query(query, job_config=job_config).result())

    def owned_tables() -> tuple[str, ...]:
        query = (
            f"SELECT table_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES` "
            "WHERE table_name = @target OR STARTS_WITH(table_name, @stage_prefix)"
        )
        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=_BIGQUERY_MAXIMUM_BYTES_BILLED,
            query_parameters=[
                bigquery.ScalarQueryParameter("target", "STRING", relation.name),
                bigquery.ScalarQueryParameter("stage_prefix", "STRING", stage_prefix),
            ],
            labels={"dander-proof": "warehouse-correctness"},
        )
        return tuple(
            str(row["table_name"]) for row in client.query(query, job_config=config).result()
        )

    def cleanup() -> None:
        for table_name in owned_tables():
            if table_name != relation.name and not table_name.startswith(stage_prefix):
                raise WarehouseCorrectnessError("BigQuery cleanup escaped its owned table prefix")
            client.delete_table(f"{project}.{dataset}.{table_name}", not_found_ok=True)

    def cleanup_verified() -> bool:
        if owned_tables():
            return False
        try:
            client.get_table(table_id)
        except NotFound:
            return True
        return False

    return _WarehouseSession(
        provider="bigquery",
        runtime=runtime,
        relation=relation,
        schema_inputs=LEGACY_BIGQUERY_FIELDS,
        transport=WriteTransport.LOAD_JOB,
        read_rows=read_rows,
        cleanup=cleanup,
        cleanup_verified=cleanup_verified,
        close=cast("Callable[[], None]", client.close),
    )


def _open_postgresql(profile: Mapping[str, object]) -> _WarehouseSession:
    from psycopg import sql

    values = dict(profile)
    schema_name = f"{_TARGET_PREFIX}{uuid.uuid4().hex[:12]}"
    values["schema"] = schema_name
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, values)
    runtime = registry.build(ProviderKind.WAREHOUSE, config)
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("PostgreSQL provider returned an invalid warehouse runtime")
    fence = cast("PostgreSQLTargetFence", runtime.target_fence)
    relation = RelationRef(
        catalog=fence.catalog,
        namespace=schema_name,
        name="records",
    )

    def read_rows() -> Sequence[Mapping[str, object]]:
        statement = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
            sql.SQL(", ").join(sql.Identifier(field.name) for field in COMMON_SCHEMA.fields),
            sql.Identifier(schema_name, relation.name),
            sql.Identifier("id"),
        )
        with fence.pool.connection() as connection:
            return tuple(cast("Mapping[str, object]", row) for row in connection.execute(statement))

    def cleanup() -> None:
        with fence.pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )

    def cleanup_verified() -> bool:
        with fence.pool.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM pg_namespace WHERE nspname = %s",
                (schema_name,),
            ).fetchone()
        return row is not None and _integer(row["count"]) == 0

    return _WarehouseSession(
        provider="postgresql",
        runtime=runtime,
        relation=relation,
        schema_inputs=COMMON_SCHEMA.fields,
        transport=WriteTransport.COPY,
        read_rows=read_rows,
        cleanup=cleanup,
        cleanup_verified=cleanup_verified,
        close=fence.pool.close,
    )


def _open_snowflake(profile: Mapping[str, object]) -> _WarehouseSession:
    from dander.providers.snowflake.session import execute, open_connection

    values = dict(profile)
    schema_name = f"DANDER_CONFORM_{uuid.uuid4().hex[:12].upper()}"
    values.update(
        {
            "schema": schema_name,
            "direct_max_rows": 10,
            "direct_max_logical_bytes": 1_048_576,
        }
    )
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, values)
    database = getattr(config, "database", None)
    if not isinstance(database, str):
        raise TypeError("Snowflake conformance profile requires a database")
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"catalog": database},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("Snowflake provider returned an invalid warehouse runtime")
    fence = cast("SnowflakeTargetFence", runtime.target_fence)
    relation = RelationRef(catalog=database, namespace=schema_name, name="records")

    def read_rows() -> Sequence[Mapping[str, object]]:
        columns = ", ".join(_quote(field.name) for field in COMMON_SCHEMA.fields)
        with open_connection(fence.connection_factory) as connection:
            rows = execute(
                connection,
                f"SELECT {columns} FROM {runtime.relation_codec.render(relation)} "
                f"ORDER BY {_quote('id')}",
                fetch="all",
            ).rows
        return _rows_from_sequences(rows)

    def cleanup() -> None:
        with open_connection(fence.connection_factory) as connection:
            execute(
                connection,
                f"DROP SCHEMA IF EXISTS {_quote(database)}.{_quote(schema_name)} CASCADE",
            )
            connection.commit()

    def cleanup_verified() -> bool:
        with open_connection(fence.connection_factory) as connection:
            row = execute(
                connection,
                f"SELECT COUNT(*) FROM {_quote(database)}.INFORMATION_SCHEMA.SCHEMATA "
                "WHERE SCHEMA_NAME = ?",
                (schema_name,),
                fetch="one",
            ).row
        return _count(row) == 0

    return _WarehouseSession(
        provider="snowflake",
        runtime=runtime,
        relation=relation,
        schema_inputs=COMMON_SCHEMA.fields,
        transport=WriteTransport.DIRECT,
        read_rows=read_rows,
        cleanup=cleanup,
        cleanup_verified=cleanup_verified,
        close=lambda: None,
    )


def _open_redshift(profile: Mapping[str, object]) -> _WarehouseSession:
    from dander.providers.redshift.session import execute, open_connection

    values = dict(profile)
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"{_TARGET_PREFIX}{suffix}"
    prefix = values.get("staging_prefix", "dander/staging")
    if not isinstance(prefix, str):
        raise ValueError("Redshift staging_prefix must be a string")
    owned_prefix = f"{prefix.strip('/')}/conformance/{suffix}"
    values.update(
        {
            "schema": schema_name,
            "staging_prefix": owned_prefix,
            "direct_max_rows": 10,
            "direct_max_logical_bytes": 1_048_576,
        }
    )
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, values)
    database = getattr(config, "database", None)
    if not isinstance(database, str):
        raise TypeError("Redshift conformance profile requires a database")
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"catalog": database},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("Redshift provider returned an invalid warehouse runtime")
    fence = cast("RedshiftTargetFence", runtime.target_fence)
    writers = cast("RedshiftWriterFactory", runtime.writers)
    s3 = cast("_S3Inspector", writers.s3_client)
    relation = RelationRef(catalog=database, namespace=schema_name, name="records")

    def read_rows() -> Sequence[Mapping[str, object]]:
        columns = _redshift_read_projection(COMMON_SCHEMA)
        with open_connection(fence.connection_factory) as connection:
            rows = execute(
                connection,
                f"SELECT {columns} FROM {runtime.relation_codec.render(relation)} "
                f"ORDER BY {_quote('id')}",
                fetch="all",
            ).rows
        return _decode_redshift_read_rows(rows)

    def owned_keys() -> tuple[str, ...]:
        keys: list[str] = []
        token: str | None = None
        while True:
            arguments: dict[str, object] = {
                "Bucket": writers.staging.bucket,
                "Prefix": f"{owned_prefix}/",
            }
            if token is not None:
                arguments["ContinuationToken"] = token
            response = s3.list_objects_v2(**arguments)
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise WarehouseCorrectnessError("Redshift cleanup returned malformed contents")
            for item in contents:
                if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                    raise WarehouseCorrectnessError("Redshift cleanup returned a malformed key")
                key = cast("str", item["Key"])
                if not key.startswith(f"{owned_prefix}/"):
                    raise WarehouseCorrectnessError("Redshift cleanup escaped its owned prefix")
                keys.append(key)
            if response.get("IsTruncated") is not True:
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise WarehouseCorrectnessError("Redshift cleanup pagination was malformed")
            token = next_token
        return tuple(keys)

    def cleanup() -> None:
        with open_connection(fence.connection_factory) as connection:
            execute(connection, f"DROP SCHEMA IF EXISTS {_quote(schema_name)} CASCADE")
            connection.commit()
        keys = owned_keys()
        for index in range(0, len(keys), 1_000):
            response = s3.delete_objects(
                Bucket=writers.staging.bucket,
                Delete={"Objects": [{"Key": key} for key in keys[index : index + 1_000]]},
            )
            if response.get("Errors"):
                raise WarehouseCorrectnessError("Redshift cleanup reported undeleted objects")

    def cleanup_verified() -> bool:
        with open_connection(fence.connection_factory) as connection:
            row = execute(
                connection,
                "SELECT COUNT(*) FROM pg_namespace WHERE nspname = %s",
                (schema_name,),
                fetch="one",
            ).row
        return _count(row) == 0 and not owned_keys()

    return _WarehouseSession(
        provider="redshift",
        runtime=runtime,
        relation=relation,
        schema_inputs=COMMON_SCHEMA.fields,
        transport=WriteTransport.DIRECT,
        read_rows=read_rows,
        cleanup=cleanup,
        cleanup_verified=cleanup_verified,
        close=lambda: None,
    )


def _redshift_read_projection(schema: RelationSchema) -> str:
    return ", ".join(
        (
            f"FROM_VARBYTE({_quote(field.name)}, 'base64') AS {_quote(field.name)}"
            if field.data_type.kind is LogicalTypeKind.BINARY
            else _quote(field.name)
        )
        for field in schema.fields
    )


def _decode_redshift_read_rows(rows: object) -> tuple[dict[str, object], ...]:
    decoded_rows = _rows_from_sequences(rows)
    binary_names = tuple(
        field.name
        for field in COMMON_SCHEMA.fields
        if field.data_type.kind is LogicalTypeKind.BINARY
    )
    for row in decoded_rows:
        for name in binary_names:
            value = row[name]
            if value is None:
                continue
            if not isinstance(value, str):
                raise WarehouseCorrectnessError("Redshift binary readback has the wrong type")
            try:
                row[name] = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError) as error:
                raise WarehouseCorrectnessError(
                    "Redshift binary readback is not valid base64"
                ) from error
    return decoded_rows


def _normalize_row(row: Mapping[str, object], schema: RelationSchema) -> dict[str, object]:
    return {
        field.name: _normalize_scalar(row[field.name], field.data_type) for field in schema.fields
    }


def _normalize_scalar(value: object, data_type: CanonicalType) -> object:
    if value is None:
        return None
    match data_type.kind:
        case LogicalTypeKind.BOOLEAN:
            if not isinstance(value, bool):
                raise WarehouseCorrectnessError("canonical boolean readback has the wrong type")
            return value
        case LogicalTypeKind.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise WarehouseCorrectnessError("canonical integer readback has the wrong type")
            return value
        case LogicalTypeKind.DECIMAL:
            try:
                decimal = value if isinstance(value, Decimal) else Decimal(str(value))
                assert data_type.scale is not None
                quantum = Decimal(1).scaleb(-data_type.scale)
                normalized = decimal.quantize(quantum)
            except (InvalidOperation, ValueError) as error:
                raise WarehouseCorrectnessError("canonical decimal readback is invalid") from error
            if normalized != decimal:
                raise WarehouseCorrectnessError("canonical decimal readback lost scale precision")
            return format(normalized, "f")
        case LogicalTypeKind.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WarehouseCorrectnessError("canonical float readback has the wrong type")
            result = float(value)
            if not math.isfinite(result):
                raise WarehouseCorrectnessError("canonical float readback must be finite")
            return 0.0 if result == 0 else result
        case LogicalTypeKind.STRING:
            if not isinstance(value, str):
                raise WarehouseCorrectnessError("canonical string readback has the wrong type")
            return unicodedata.normalize("NFC", value)
        case LogicalTypeKind.BINARY:
            if isinstance(value, memoryview):
                value = value.tobytes()
            if isinstance(value, bytearray):
                value = bytes(value)
            if not isinstance(value, bytes):
                raise WarehouseCorrectnessError("canonical binary readback has the wrong type")
            return base64.b64encode(value).decode("ascii")
        case LogicalTypeKind.DATE:
            if isinstance(value, datetime) or not isinstance(value, date):
                raise WarehouseCorrectnessError("canonical date readback has the wrong type")
            return value.isoformat()
        case LogicalTypeKind.TIME:
            if not isinstance(value, time) or value.utcoffset() is not None:
                raise WarehouseCorrectnessError("canonical time readback has the wrong type")
            return value.isoformat(timespec="microseconds")
        case LogicalTypeKind.TIMESTAMP:
            if not isinstance(value, datetime):
                raise WarehouseCorrectnessError("canonical timestamp readback has the wrong type")
            if data_type.with_timezone:
                if value.utcoffset() is None:
                    raise WarehouseCorrectnessError(
                        "canonical zoned timestamp readback lost its timezone"
                    )
                return (
                    value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
                )
            if value.utcoffset() is not None:
                raise WarehouseCorrectnessError(
                    "canonical local timestamp readback gained a timezone"
                )
            return value.isoformat(timespec="microseconds")
        case _:
            raise WarehouseCorrectnessError(
                "fixture contains a type outside the common scalar intersection"
            )


def _without_extensions(schema: RelationSchema) -> RelationSchema:
    return RelationSchema(
        fields=tuple(field.model_copy(update={"extensions": ()}) for field in schema.fields)
    )


def _rows_from_sequences(rows: object) -> tuple[dict[str, object], ...]:
    if not isinstance(rows, Sequence):
        raise WarehouseCorrectnessError("provider readback did not return a row sequence")
    names = tuple(field.name for field in COMMON_SCHEMA.fields)
    result: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
            raise WarehouseCorrectnessError("provider readback returned a malformed row")
        if len(row) != len(names):
            raise WarehouseCorrectnessError("provider readback returned the wrong column count")
        result.append(dict(zip(names, row, strict=True)))
    return tuple(result)


def _require_transport(
    session: _WarehouseSession,
    operations: Sequence[OperationTelemetry],
) -> None:
    observed = tuple(
        operation.transport for operation in operations if operation.transport is not None
    )
    if observed and any(item is not session.transport for item in observed):
        raise WarehouseCorrectnessError(
            f"{session.provider} conformance used an unexpected transport"
        )
    if session.provider in {"snowflake", "redshift"} and not observed:
        raise WarehouseCorrectnessError(
            f"{session.provider} conformance emitted no transport evidence"
        )


def _fixture_hash() -> str:
    payload = {
        "version": _FIXTURE_VERSION,
        "initial": normalized_rows(_INITIAL_ROWS),
        "update": normalized_rows(_UPDATE_ROWS),
        "expected": normalized_rows(_EXPECTED_ROWS),
    }
    return _hash_json(payload)


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _count(row: object) -> int:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)) or not row:
        raise WarehouseCorrectnessError("provider cleanup count was malformed")
    return _integer(row[0])


def _integer(value: object) -> int:
    if isinstance(value, bool):
        raise WarehouseCorrectnessError("provider cleanup count was not an integer")
    if isinstance(value, int):
        return value
    if not isinstance(value, (str, bytes, bytearray)):
        raise WarehouseCorrectnessError("provider cleanup count was not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise WarehouseCorrectnessError("provider cleanup count was not an integer") from error


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("JSON input must contain one object")
    return cast("Mapping[str, object]", value)


def _write_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content}\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one provider and write sanitized evidence")
    run.add_argument("--profile-json", type=Path, required=True)
    run.add_argument("--candidate-commit", required=True)
    run.add_argument("--approved-cost-ceiling-usd", required=True)
    run.add_argument("--cost-approval-reference", required=True)
    run.add_argument("--output", type=Path, required=True)
    compare = subparsers.add_parser("compare", help="require equal evidence from all providers")
    compare.add_argument("--evidence", type=Path, action="append", required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "compare":
        try:
            records = tuple(
                WarehouseCorrectnessEvidence.from_mapping(_load_object(path))
                for path in arguments.evidence
            )
            comparison = compare_evidence(records)
            _write_json(arguments.output, comparison.to_json())
        except Exception:
            print(
                json.dumps(
                    {
                        "schema": _COMPARISON_SCHEMA,
                        "status": "failed",
                        "summary": "Four-warehouse correctness evidence did not compare equal.",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 1
        print(comparison.to_json())
        return 0

    profile: Mapping[str, object] = {}
    try:
        profile = _load_object(arguments.profile_json)
        ceiling = ApprovedCostCeiling(
            arguments.approved_cost_ceiling_usd,
            arguments.cost_approval_reference,
        )
        provider_evidence = run_provider(
            profile,
            candidate_commit=arguments.candidate_commit,
            cost_ceiling=ceiling,
        )
        _write_json(arguments.output, provider_evidence.to_json())
    except _WarehouseCorrectnessRunError as error:
        _write_json(arguments.output, error.evidence.to_json())
        print(error.evidence.to_json())
        return 1
    except Exception:
        provider = profile.get("provider")
        print(
            json.dumps(
                {
                    "schema": _EVIDENCE_SCHEMA,
                    "provider": provider if provider in _PROVIDERS else "unknown",
                    "status": "failed",
                    "summary": (
                        "Warehouse correctness run failed; inspect provider logs and verify "
                        "owned cleanup before retrying."
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    print(provider_evidence.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
