"""Bounded, checksummed Parquet artifacts shared by staged warehouse loaders."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Self, cast

from dander.warehouse.contracts import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    RelationSchema,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    import pyarrow as pa

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,126}$")
_MANIFEST_SCHEMA = "io.dander.warehouse.staging/v1"


class StagingArtifactError(ValueError):
    """Raised when rows or local artifact lifecycle violate the staging contract."""


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    """One immutable Parquet part and its content identity."""

    path: Path
    rows: int
    logical_bytes: int
    compressed_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.path.name,
            "rows": self.rows,
            "logical_bytes": self.logical_bytes,
            "compressed_bytes": self.compressed_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class StagingManifest:
    """Deterministic, non-sensitive manifest for one run-scoped artifact set."""

    run_id: str
    schema_fingerprint: str
    artifacts: tuple[StagedArtifact, ...]
    schema: str = _MANIFEST_SCHEMA

    @property
    def rows(self) -> int:
        return sum(artifact.rows for artifact in self.artifacts)

    @property
    def logical_bytes(self) -> int:
        return sum(artifact.logical_bytes for artifact in self.artifacts)

    @property
    def compressed_bytes(self) -> int:
        return sum(artifact.compressed_bytes for artifact in self.artifacts)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": self.schema,
                "run_id": self.run_id,
                "schema_fingerprint": self.schema_fingerprint,
                "rows": self.rows,
                "logical_bytes": self.logical_bytes,
                "compressed_bytes": self.compressed_bytes,
                "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            },
            separators=(",", ":"),
            sort_keys=True,
        )


class ParquetStagingSession:
    """Create one exclusive run directory and remove only its owned files on exit."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        max_rows_per_file: int = 100_000,
        max_logical_bytes_per_file: int = 128 * 1_024 * 1_024,
        compression: str = "zstd",
    ) -> None:
        if _RUN_ID.fullmatch(run_id) is None:
            raise StagingArtifactError("staging run id is invalid")
        if not 1 <= max_rows_per_file <= 1_000_000:
            raise StagingArtifactError("max_rows_per_file must be between 1 and 1000000")
        if not 1_024 <= max_logical_bytes_per_file <= 1_073_741_824:
            raise StagingArtifactError(
                "max_logical_bytes_per_file must be between 1024 and 1073741824"
            )
        if compression not in {"snappy", "zstd"}:
            raise StagingArtifactError("Parquet compression must be snappy or zstd")
        self._root = root.expanduser().resolve()
        self._directory = self._root / run_id
        self._run_id = run_id
        self._max_rows = max_rows_per_file
        self._max_bytes = max_logical_bytes_per_file
        self._compression = compression
        self._entered = False
        self._staged = False

    @property
    def directory(self) -> Path:
        return self._directory

    def __enter__(self) -> Self:
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            self._directory.mkdir(mode=0o700)
        except FileExistsError as error:
            raise StagingArtifactError(
                f"staging directory already exists for run {self._run_id!r}"
            ) from error
        self._entered = True
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()

    def stage(
        self,
        records: Iterable[Mapping[str, object]],
        schema: RelationSchema,
    ) -> StagingManifest:
        """Consume rows once and write bounded immutable Parquet parts."""
        if not self._entered or not self._directory.is_dir():
            raise StagingArtifactError("Parquet staging session is not active")
        if self._staged:
            raise StagingArtifactError("Parquet staging session may stage only once")
        self._staged = True
        arrow_schema = _arrow_schema(schema)
        artifacts: list[StagedArtifact] = []
        chunk: list[dict[str, object]] = []
        chunk_bytes = 0
        index = 0
        for row_index, record in enumerate(records):
            normalized = _normalize_record(record, schema.fields, row_index=row_index)
            row_bytes = _logical_size(normalized)
            if chunk and (
                len(chunk) >= self._max_rows or chunk_bytes + row_bytes > self._max_bytes
            ):
                artifacts.append(
                    self._write_part(
                        arrow_schema,
                        chunk,
                        logical_bytes=chunk_bytes,
                        index=index,
                    )
                )
                index += 1
                chunk = []
                chunk_bytes = 0
            chunk.append(normalized)
            chunk_bytes += row_bytes
        if chunk:
            artifacts.append(
                self._write_part(
                    arrow_schema,
                    chunk,
                    logical_bytes=chunk_bytes,
                    index=index,
                )
            )
        return StagingManifest(
            run_id=self._run_id,
            schema_fingerprint=hashlib.sha256(
                schema.model_dump_json(by_alias=True).encode("utf-8")
            ).hexdigest(),
            artifacts=tuple(artifacts),
        )

    def cleanup(self) -> None:
        """Delete only regular files inside this session's exact run directory."""
        if not self._directory.exists():
            return
        for path in self._directory.iterdir():
            if not path.is_file() or path.is_symlink():
                raise StagingArtifactError("staging cleanup found an unexpected path")
            path.unlink()
        self._directory.rmdir()

    def _write_part(
        self,
        arrow_schema: pa.Schema,
        records: list[dict[str, object]],
        *,
        logical_bytes: int,
        index: int,
    ) -> StagedArtifact:
        import pyarrow as arrow
        import pyarrow.parquet as parquet

        temporary = self._directory / f".part-{index:05d}.parquet.tmp"
        try:
            table = arrow.Table.from_pylist(records, schema=arrow_schema)
            parquet.write_table(  # type: ignore[no-untyped-call]
                table,
                temporary,
                compression=self._compression,
                use_dictionary=True,
                write_statistics=True,
            )
        except Exception as error:
            if temporary.exists():
                temporary.unlink()
            raise StagingArtifactError(
                f"staging part {index} does not conform to the declared schema"
            ) from error
        os.chmod(temporary, 0o600)
        checksum = _file_sha256(temporary)
        final = self._directory / f"part-{index:05d}-{checksum[:16]}.parquet"
        temporary.replace(final)
        return StagedArtifact(
            path=final,
            rows=len(records),
            logical_bytes=logical_bytes,
            compressed_bytes=final.stat().st_size,
            sha256=checksum,
        )


def _arrow_schema(schema: RelationSchema) -> pa.Schema:
    try:
        import pyarrow as arrow
    except ModuleNotFoundError as error:
        raise StagingArtifactError(
            "Parquet staging requires pyarrow; install a Snowflake or Redshift runtime extra"
        ) from error
    return arrow.schema(
        [
            arrow.field(
                field.name,
                _arrow_type(field.data_type),
                nullable=field.cardinality is FieldCardinality.NULLABLE,
            )
            for field in schema.fields
        ]
    )


def _arrow_type(data_type: CanonicalType) -> pa.DataType:
    import pyarrow as arrow

    match data_type.kind:
        case LogicalTypeKind.BOOLEAN:
            return arrow.bool_()
        case LogicalTypeKind.INTEGER:
            assert data_type.bit_width is not None
            return {
                8: arrow.int8(),
                16: arrow.int16(),
                32: arrow.int32(),
                64: arrow.int64(),
            }[data_type.bit_width]
        case LogicalTypeKind.DECIMAL:
            assert data_type.precision is not None and data_type.scale is not None
            if data_type.precision <= 38:
                return arrow.decimal128(data_type.precision, data_type.scale)
            if data_type.precision <= 76:
                return arrow.decimal256(data_type.precision, data_type.scale)
            raise StagingArtifactError("Parquet decimal precision cannot exceed 76")
        case LogicalTypeKind.FLOAT:
            return arrow.float32() if data_type.bit_width == 32 else arrow.float64()
        case LogicalTypeKind.STRING | LogicalTypeKind.JSON:
            return arrow.string()
        case LogicalTypeKind.BINARY:
            return arrow.binary()
        case LogicalTypeKind.DATE:
            return arrow.date32()
        case LogicalTypeKind.TIME:
            assert data_type.fractional_second_precision is not None
            unit = "us" if data_type.fractional_second_precision <= 6 else "ns"
            return arrow.time64(unit)
        case LogicalTypeKind.TIMESTAMP:
            assert data_type.fractional_second_precision is not None
            precision = data_type.fractional_second_precision
            unit = "ms" if precision <= 3 else "us" if precision <= 6 else "ns"
            return arrow.timestamp(unit, tz="UTC" if data_type.with_timezone else None)
        case LogicalTypeKind.ARRAY:
            assert data_type.element is not None
            return arrow.list_(_arrow_type(data_type.element))
        case LogicalTypeKind.RECORD:
            return arrow.struct(
                [
                    arrow.field(
                        field.name,
                        _arrow_type(field.data_type),
                        nullable=field.cardinality is FieldCardinality.NULLABLE,
                    )
                    for field in data_type.fields
                ]
            )
    raise AssertionError("Unhandled canonical Parquet type")


def _normalize_record(
    record: Mapping[str, object],
    fields: tuple[CanonicalField, ...],
    *,
    row_index: int,
) -> dict[str, object]:
    expected = {field.name for field in fields}
    if set(record) != expected:
        raise StagingArtifactError(f"staging record {row_index} does not match the declared schema")
    return {
        field.name: _normalize_value(record[field.name], field, row_index=row_index)
        for field in fields
    }


def _normalize_value(value: object, field: CanonicalField, *, row_index: int) -> object:
    if value is None:
        if field.cardinality is FieldCardinality.REQUIRED:
            raise StagingArtifactError(f"staging record {row_index} has a null required field")
        return None
    return _normalize_type_value(value, field.data_type, row_index=row_index)


def _normalize_type_value(value: object, data_type: CanonicalType, *, row_index: int) -> object:
    if data_type.kind is LogicalTypeKind.JSON:
        try:
            return json.dumps(value, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as error:
            raise StagingArtifactError(
                f"staging record {row_index} contains invalid JSON"
            ) from error
    if data_type.kind is LogicalTypeKind.ARRAY:
        if not isinstance(value, (list, tuple)):
            raise StagingArtifactError(f"staging record {row_index} contains an invalid array")
        assert data_type.element is not None
        return [
            _normalize_type_value(item, data_type.element, row_index=row_index)
            if item is not None
            else None
            for item in value
        ]
    if data_type.kind is LogicalTypeKind.RECORD:
        if not isinstance(value, dict):
            raise StagingArtifactError(f"staging record {row_index} contains an invalid record")
        return _normalize_record(
            cast("Mapping[str, object]", value),
            data_type.fields,
            row_index=row_index,
        )
    return value


def _logical_size(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return 8
    if isinstance(value, Decimal):
        return len(str(value))
    if isinstance(value, (date, datetime, time)):
        return len(value.isoformat())
    if isinstance(value, (list, tuple)):
        return sum(_logical_size(item) for item in value)
    if isinstance(value, dict):
        return sum(
            len(str(key).encode("utf-8")) + _logical_size(item) for key, item in value.items()
        )
    return len(str(value).encode("utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ParquetStagingSession",
    "StagedArtifact",
    "StagingArtifactError",
    "StagingManifest",
]
