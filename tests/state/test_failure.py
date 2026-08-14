"""Operator-safe run failure classification."""

from __future__ import annotations

from dataclasses import dataclass

from dander.state import (
    RunStage,
    classify_failure,
    failure_diagnostic_was_logged,
    mark_failure_diagnostic_logged,
)
from dander.warehouse import WarehouseSchemaSupportError


@dataclass
class _Response:
    status_code: int


class _HttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.response = _Response(status_code)


class _BotocoreStyleError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status_code}}


def test_failure_classifier_uses_http_status_without_persisting_exception_text() -> None:
    error = RuntimeError("wrapper with token=top-secret")
    error.__cause__ = _HttpError(401, "private-key=also-secret")

    failure = classify_failure(error, stage=RunStage.INGEST, run_id="safe-run")

    assert failure.code == "authentication_failed"
    assert "top-secret" not in failure.summary
    assert "also-secret" not in failure.summary
    assert len(failure.summary) <= 512
    assert failure.exception_class_chain == ("RuntimeError", "_HttpError")
    assert failure.status_code == 401
    assert failure.diagnostic_payload(run_id="safe-run", stage="ingest") == {
        "run_id": "safe-run",
        "stage": "ingest",
        "failure_code": "authentication_failed",
        "exception_class_chain": ["RuntimeError", "_HttpError"],
        "status_code": 401,
    }


def test_failure_diagnostic_marker_survives_exception_wrapping() -> None:
    executor_error = RuntimeError("private executor detail")
    mark_failure_diagnostic_logged(executor_error)
    wrapper = RuntimeError("private wrapper detail")
    wrapper.__cause__ = executor_error

    assert failure_diagnostic_was_logged(wrapper)
    assert not failure_diagnostic_was_logged(RuntimeError("pre-executor"))


def test_failure_classifier_normalizes_direct_provider_status() -> None:
    class ServiceError(RuntimeError):
        status = 429

    failure = classify_failure(
        ServiceError("OCI request detail must not persist"),
        stage=RunStage.INGEST,
        run_id="safe-run",
    )

    assert failure.code == "rate_limited"
    assert "OCI request detail" not in failure.summary
    assert failure.status_code == 429


def test_failure_classifier_uses_botocore_mapping_status_without_exception_text() -> None:
    failure = classify_failure(
        _BotocoreStyleError(403, "secret-id=must-not-enter-diagnostics"),
        stage=RunStage.INGEST,
        run_id="safe-run",
    )

    assert failure.code == "permission_denied"
    assert failure.status_code == 403
    assert "must-not-enter-diagnostics" not in failure.summary


def test_failure_diagnostic_bounds_and_sanitizes_exception_class_chain() -> None:
    unsafe_error = type("Sensitive:value-must-not-escape", (RuntimeError,), {})
    current: BaseException = unsafe_error("message-must-not-escape")
    for index in range(10):
        wrapper = RuntimeError(f"private-{index}")
        wrapper.__cause__ = current
        current = wrapper

    assert isinstance(current, Exception)
    failure = classify_failure(current, stage=RunStage.INGEST, run_id="safe-run")

    assert len(failure.exception_class_chain) == 8
    assert failure.exception_class_chain == ("RuntimeError",) * 8
    assert "message-must-not-escape" not in str(
        failure.diagnostic_payload(run_id="safe-run", stage="ingest")
    )

    direct = classify_failure(
        unsafe_error("message-must-not-escape"),
        stage=RunStage.INGEST,
        run_id="safe-run",
    )
    assert direct.exception_class_chain == ("Exception",)


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

    unexpected = classify_failure(
        RuntimeError("customer payload must never persist"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )
    assert unexpected.summary == (
        "The ingest stage failed unexpectedly. Inspect launcher logs for run run-123."
    )
    assert "Cloud Run" not in unexpected.summary


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


def test_warehouse_schema_support_failures_keep_the_source_schema_code() -> None:
    failure = classify_failure(
        WarehouseSchemaSupportError("private provider schema detail"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )

    assert failure.code == "source_schema_failed"
    assert failure.summary == (
        "The declared source schema is unsupported or a record did not match it. "
        "Inspect logs for run run-123."
    )
    assert "private" not in failure.summary


def test_provider_specific_failures_use_cloud_neutral_summaries() -> None:
    class PermissionDenied(RuntimeError):  # noqa: N818 - matches provider SDK class name
        pass

    class BigQueryWriteError(RuntimeError):
        pass

    class DestinationWriteError(RuntimeError):
        pass

    permission = classify_failure(
        PermissionDenied("private permission detail"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )
    destination = classify_failure(
        BigQueryWriteError("private row detail"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )
    portable_destination = classify_failure(
        DestinationWriteError("private row detail"),
        stage=RunStage.INGEST,
        run_id="run-123",
    )

    assert permission.summary == (
        "Permission was denied. Verify the selected provider permissions for this pipeline."
    )
    assert destination.summary == (
        "The destination rejected a write. Inspect logs for run run-123."
    )
    assert portable_destination.code == "destination_write_failed"
    assert portable_destination.summary == destination.summary
    assert "GCP" not in permission.summary
    assert "BigQuery" not in destination.summary
