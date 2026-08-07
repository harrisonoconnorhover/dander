"""Operator-safe run failure classification."""

from __future__ import annotations

from dataclasses import dataclass

from dander.state import RunStage, classify_failure


@dataclass
class _Response:
    status_code: int


class _HttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.response = _Response(status_code)


def test_failure_classifier_uses_http_status_without_persisting_exception_text() -> None:
    error = RuntimeError("wrapper with token=top-secret")
    error.__cause__ = _HttpError(401, "private-key=also-secret")

    failure = classify_failure(error, stage=RunStage.INGEST, run_id="safe-run")

    assert failure.code == "authentication_failed"
    assert "top-secret" not in failure.summary
    assert "also-secret" not in failure.summary
    assert len(failure.summary) <= 512


def test_unknown_failure_is_stage_specific_and_points_to_run_logs() -> None:
    failure = classify_failure(
        RuntimeError("customer payload must never persist"),
        stage=RunStage.METADATA,
        run_id="run-123",
    )

    assert failure.code == "catalog_failed"
    assert failure.summary == (
        "Metadata or catalog publication failed. Inspect logs for run run-123."
    )
    assert "customer payload" not in failure.summary


def test_runtime_cancellation_is_safe_and_retryable_by_a_fresh_run() -> None:
    class RuntimeCancelledError(RuntimeError):
        pass

    failure = classify_failure(
        RuntimeCancelledError("signal detail must not persist"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )

    assert failure.code == "interrupted_run"
    assert failure.summary == (
        "The runtime received a cancellation signal. A fresh run can retry safely; "
        "inspect logs for run run-123."
    )
    assert "signal detail" not in failure.summary


def test_wrapped_runtime_stage_errors_keep_stable_codes() -> None:
    class TransformRunError(RuntimeError):
        pass

    class CatalogPublishError(RuntimeError):
        pass

    transform_wrapper = RuntimeError("wrapper")
    transform_wrapper.__cause__ = TransformRunError("private transform detail")
    catalog_wrapper = RuntimeError("wrapper")
    catalog_wrapper.__cause__ = CatalogPublishError("private catalog detail")

    transform = classify_failure(transform_wrapper, stage=RunStage.INGEST, run_id="run-123")
    catalog = classify_failure(catalog_wrapper, stage=RunStage.INGEST, run_id="run-123")

    assert transform.code == "transform_failed"
    assert catalog.code == "catalog_failed"
    assert "private" not in transform.summary
    assert "private" not in catalog.summary
