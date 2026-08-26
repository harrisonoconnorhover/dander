"""Sanitized operator-facing failure summaries for the durable run ledger."""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dander.state.run_history import RunStage

_MAX_SUMMARY_LENGTH = 512
_MAX_EXCEPTION_CHAIN = 8
_MAX_EXCEPTION_CLASS_LENGTH = 128
_SAFE_EXCEPTION_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DIAGNOSTIC_COUNT: ContextVar[int] = ContextVar("dander_failure_diagnostic_count", default=0)


@dataclass(frozen=True)
class FailureDetails:
    """A stable failure plus bounded, message-free diagnostic identity."""

    code: str
    summary: str
    exception_class_chain: tuple[str, ...] = ()
    status_code: int | None = None

    def diagnostic_payload(self, *, run_id: str, stage: str) -> dict[str, object]:
        """Return safe causal identity for launcher logs, never durable history."""
        return {
            "run_id": run_id,
            "stage": stage,
            "failure_code": self.code,
            "exception_class_chain": list(self.exception_class_chain),
            "status_code": self.status_code,
        }


def classify_failure(error: Exception, *, stage: RunStage, run_id: str) -> FailureDetails:
    """Classify an exception without copying unrestricted exception text into history."""
    chain = _exception_chain(error)
    names = {type(item).__name__ for item in chain}
    statuses = {status for item in chain if (status := _status_code(item)) is not None}

    def details(code: str, summary: str) -> FailureDetails:
        return _details(code, summary, chain=chain)

    if "RuntimeCancelledError" in names:
        return details(
            "interrupted_run",
            f"The runtime received a cancellation signal. A fresh run can retry safely; "
            f"inspect logs for run {run_id}.",
        )
    if 401 in statuses or names & {
        "FargateIdentityError",
        "NoCredentialsError",
        "PartialCredentialsError",
        "Unauthenticated",
        "Unauthorized",
    }:
        return details(
            "authentication_failed",
            "Authentication failed. Verify the configured secret and provider credentials.",
        )
    if 403 in statuses or names & {"Forbidden", "PermissionDenied"}:
        return details(
            "permission_denied",
            "Permission was denied. Verify the selected provider permissions for this pipeline.",
        )
    if 429 in statuses or names & {"TooManyRequests", "ResourceExhausted"}:
        return details(
            "rate_limited",
            "The source throttled this run after bounded retries. Retry after its limit resets.",
        )
    if names & {"LeaseLostError", "WatermarkConflictError"}:
        return details(
            "lease_failed",
            "Pipeline ownership or cursor fencing was lost. A fresh run can retry safely.",
        )
    if "RedshiftConnectionUnavailableError" in names:
        return details(
            "destination_write_failed",
            "Redshift was temporarily unavailable. A fresh run can retry safely.",
        )
    if names & {"RawSchemaError", "CursorValueError", "WarehouseSchemaSupportError"}:
        return details(
            "source_schema_failed",
            f"The declared source schema is unsupported or a record did not match it. "
            f"Inspect logs for run {run_id}.",
        )
    if names & {"BigQueryWriteError", "DestinationWriteError", "WarehouseWriteError"}:
        return details(
            "destination_write_failed",
            f"The destination rejected a write. Inspect logs for run {run_id}.",
        )
    if names & {"TransformProjectError", "TransformRunError", "GraphRuntimeError"}:
        return details(
            "transform_failed",
            f"A transform failed. Inspect logs for run {run_id}.",
        )
    if names & {"CatalogPublishError", "SemanticRegistryError"}:
        return details(
            "catalog_failed",
            f"Metadata or catalog publication failed. Inspect logs for run {run_id}.",
        )
    if stage.value == "transform":
        code = (
            "test_failed" if _has_known_phrase(chain, "data tests failed") else "transform_failed"
        )
        action = "Data tests failed" if code == "test_failed" else "A transform failed"
        return details(code, f"{action}. Inspect logs for run {run_id}.")
    if stage.value == "metadata":
        return details(
            "catalog_failed",
            f"Metadata or catalog publication failed. Inspect logs for run {run_id}.",
        )
    if "EnterpriseSourceError" in names:
        if _has_known_phrase(chain, "authentication failed"):
            return details(
                "authentication_failed",
                "Authentication failed. Verify the configured secret and provider credentials.",
            )
        if _has_known_phrase(chain, "permission denied"):
            return details(
                "permission_denied",
                "Permission was denied. Verify the provider permissions for this pipeline.",
            )
        return details(
            "extraction_failed",
            f"Source extraction failed after bounded retries. Inspect logs for run {run_id}.",
        )
    return details(
        "unexpected_error",
        f"The {stage.value} stage failed unexpectedly. Inspect launcher logs for run {run_id}.",
    )


def failure_diagnostic_checkpoint() -> int:
    """Capture the current task-local diagnostic count before execution starts."""
    return _DIAGNOSTIC_COUNT.get()


def mark_failure_diagnostic_logged() -> None:
    """Record that the current task emitted an authoritative failure diagnostic."""
    _DIAGNOSTIC_COUNT.set(_DIAGNOSTIC_COUNT.get() + 1)


def failure_diagnostic_was_logged_since(checkpoint: int) -> bool:
    """Return whether the current task emitted a diagnostic after ``checkpoint``."""
    return _DIAGNOSTIC_COUNT.get() > checkpoint


def _details(
    code: str,
    summary: str,
    *,
    chain: tuple[BaseException, ...],
) -> FailureDetails:
    cleaned = " ".join(summary.split())[:_MAX_SUMMARY_LENGTH]
    return FailureDetails(
        code=code,
        summary=cleaned,
        exception_class_chain=tuple(_safe_exception_class(item) for item in chain),
        status_code=next(
            (status for item in chain if (status := _status_code(item)) is not None),
            None,
        ),
    )


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < _MAX_EXCEPTION_CHAIN:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _status_code(error: BaseException) -> int | None:
    response = _safe_attribute(error, "response")
    response_metadata = _safe_mapping_value(response, "ResponseMetadata")
    for value in (
        _safe_attribute(response, "status_code"),
        _safe_mapping_value(response_metadata, "HTTPStatusCode"),
        _safe_attribute(error, "status_code"),
        # OCI SDK ServiceError and several provider SDKs expose the status directly.
        _safe_attribute(error, "status"),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return int(value)
    return None


def _safe_attribute(value: object | None, name: str) -> object | None:
    if value is None:
        return None
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _safe_mapping_value(value: object | None, key: str) -> object | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return value.get(key)
    except Exception:
        return None


def _safe_exception_class(error: BaseException) -> str:
    name = type(error).__name__
    if (
        len(name) <= _MAX_EXCEPTION_CLASS_LENGTH
        and _SAFE_EXCEPTION_CLASS.fullmatch(name) is not None
    ):
        return name
    return "Exception"


def _has_known_phrase(chain: tuple[BaseException, ...], phrase: str) -> bool:
    return any(phrase in str(error).lower() for error in chain)
