"""Shared Parquet staging is bounded, deterministic, sanitized, and run-scoped."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pyarrow.parquet as parquet
import pytest

from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ParquetStagingSession,
    RelationSchema,
    StagingArtifactError,
)
from dander.writer import WriteField

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _schema() -> RelationSchema:
    return RelationSchema(
        fields=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED").to_canonical(),
            WriteField(name="amount", data_type="NUMERIC").to_canonical(),
            WriteField(name="observed_at", data_type="TIMESTAMP").to_canonical(),
            WriteField(name="payload", data_type="JSON").to_canonical(),
            WriteField(name="tags", data_type="STRING", mode="REPEATED").to_canonical(),
            CanonicalField(
                name="details",
                data_type=CanonicalType(
                    kind=LogicalTypeKind.RECORD,
                    fields=(
                        CanonicalField(
                            name="active",
                            data_type=CanonicalType(kind=LogicalTypeKind.BOOLEAN),
                            cardinality=FieldCardinality.REQUIRED,
                        ),
                        CanonicalField(
                            name="note",
                            data_type=CanonicalType(kind=LogicalTypeKind.STRING),
                        ),
                    ),
                ),
            ),
        )
    )


def _records(count: int) -> Iterator[dict[str, object]]:
    for index in range(count):
        yield {
            "id": f"row-{index}",
            "amount": Decimal(f"{index}.000000000"),
            "observed_at": datetime(2026, 8, 8, 12, index, tzinfo=UTC),
            "payload": {"index": index, "ready": True},
            "tags": ["fixture", str(index)],
            "details": {"active": True, "note": None},
        }


def test_parquet_staging_splits_parts_checksums_and_cleans_only_its_run(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    sibling = root / "operator-owned.txt"
    sibling.write_text("keep", encoding="utf-8")

    with ParquetStagingSession(
        root,
        run_id="run-123",
        max_rows_per_file=2,
        max_logical_bytes_per_file=1_048_576,
    ) as session:
        manifest = session.stage(_records(5), _schema())

        assert manifest.rows == 5
        assert len(manifest.artifacts) == 3
        assert [artifact.rows for artifact in manifest.artifacts] == [2, 2, 1]
        assert manifest.logical_bytes > 0
        assert manifest.compressed_bytes > 0
        assert len(manifest.schema_fingerprint) == 64
        for index, artifact in enumerate(manifest.artifacts):
            assert artifact.path.name.startswith(f"part-{index:05d}-{artifact.sha256[:16]}")
            assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
            assert stat.S_IMODE(artifact.path.stat().st_mode) == 0o600
        table = parquet.read_table(  # type: ignore[no-untyped-call]
            [artifact.path for artifact in manifest.artifacts]
        )
        assert table.num_rows == 5
        assert table.column("id").to_pylist() == [f"row-{index}" for index in range(5)]
        assert json.loads(table.column("payload")[0].as_py()) == {"index": 0, "ready": True}
        serialized = manifest.to_json()
        assert str(root) not in serialized
        assert "operator-owned" not in serialized

    assert not session.directory.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_staging_is_single_use_and_empty_input_is_explicit(tmp_path: Path) -> None:
    with ParquetStagingSession(tmp_path, run_id="empty") as session:
        manifest = session.stage((), _schema())

        assert manifest.rows == 0
        assert manifest.artifacts == ()
        with pytest.raises(StagingArtifactError, match="only once"):
            session.stage((), _schema())


def test_staging_splits_on_logical_bytes_without_materializing_the_endpoint(tmp_path: Path) -> None:
    schema = RelationSchema(
        fields=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED").to_canonical(),
            WriteField(name="payload", data_type="STRING").to_canonical(),
        )
    )

    def records() -> Iterator[dict[str, object]]:
        for index in range(3):
            yield {"id": str(index), "payload": "x" * 800}

    with ParquetStagingSession(
        tmp_path,
        run_id="byte-split",
        max_rows_per_file=100,
        max_logical_bytes_per_file=1_024,
    ) as session:
        manifest = session.stage(records(), schema)

        assert [artifact.rows for artifact in manifest.artifacts] == [1, 1, 1]


def test_staging_failure_is_sanitized_and_cleanup_runs(tmp_path: Path) -> None:
    private_value = "private-record-value"
    session = ParquetStagingSession(tmp_path, run_id="invalid")

    with pytest.raises(StagingArtifactError, match="record 0") as raised, session:
        session.stage(
            ({"id": private_value},),
            _schema(),
        )

    assert private_value not in str(raised.value)
    assert not session.directory.exists()


def test_staging_refuses_existing_run_directory_and_unsupported_decimal(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    with (
        pytest.raises(StagingArtifactError, match="already exists"),
        ParquetStagingSession(tmp_path, run_id="existing"),
    ):
        raise AssertionError("unreachable")

    oversized_decimal = RelationSchema(
        fields=(
            CanonicalField(
                name="amount",
                data_type=CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=77, scale=2),
            ),
        )
    )
    with (
        pytest.raises(StagingArtifactError, match="precision cannot exceed 76"),
        ParquetStagingSession(tmp_path, run_id="decimal") as session,
    ):
        session.stage(({"amount": Decimal("1.00")},), oversized_decimal)
