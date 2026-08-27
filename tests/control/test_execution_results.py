"""Provider-neutral runtime completion result collection."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dander.control.execution_results import (
    ExecutionResultCollectionError,
    collect_execution_result_summary,
    parse_execution_result_summary,
)
from dander.control.orchestration import BackendLogPage, BackendLogRecord
from dander.runtime_contract import RUNTIME_CONTRACT

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def _message(*, extracted_rows: int = 3) -> str:
    return json.dumps(
        {
            "contract": RUNTIME_CONTRACT,
            "event": "runtime.completed",
            "pipeline_id": "hosted_graph",
            "status": "succeeded",
            "outputs": {
                "metrics": {
                    "endpoints": 1,
                    "extracted_rows": extracted_rows,
                    "affected_rows": 3,
                    "models": 1,
                    "assertions": 3,
                    "assets": 1,
                },
                "telemetry": {
                    "duration_ms": 1_000,
                    "retry_count": 0,
                    "rows_read": 3,
                    "rows_written": 3,
                    "rows_affected": 3,
                    "bytes_read": 30,
                    "bytes_written": 30,
                    "bytes_processed": 30,
                    "bytes_billed": 0,
                    "queue_duration_ms": 0,
                    "execution_duration_ms": 10,
                    "spill_bytes": 0,
                    "operations": [{"operation": "extract"}, {"operation": "load"}],
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def test_runtime_completion_normalizes_to_one_fixed_size_summary() -> None:
    summary = parse_execution_result_summary(_message(), pipeline_id="hosted_graph")

    assert summary is not None
    assert summary.extracted_rows == 3
    assert summary.operation_count == 2
    assert summary.duration_ms == 1_000
    assert not hasattr(summary, "operations")
    assert parse_execution_result_summary("worker started", pipeline_id="hosted_graph") is None


def test_collection_follows_bounded_pages_until_the_completion_event() -> None:
    pages = {
        None: BackendLogPage(
            records=(BackendLogRecord(NOW, "worker started"),),
            next_cursor="page-2",
        ),
        "page-2": BackendLogPage(records=(BackendLogRecord(NOW, _message()),)),
    }
    calls: list[tuple[str | None, int]] = []

    def read_page(cursor: str | None, limit: int) -> BackendLogPage:
        calls.append((cursor, limit))
        return pages[cursor]

    summary = collect_execution_result_summary(read_page, pipeline_id="hosted_graph")

    assert summary.extracted_rows == 3
    assert calls == [(None, 500), ("page-2", 500)]


def test_matching_malformed_or_cross_pipeline_completion_is_rejected() -> None:
    with pytest.raises(ExecutionResultCollectionError, match="non-negative"):
        parse_execution_result_summary(_message(extracted_rows=-1), pipeline_id="hosted_graph")
    with pytest.raises(ExecutionResultCollectionError, match="execution plan"):
        parse_execution_result_summary(_message(), pipeline_id="different_graph")
