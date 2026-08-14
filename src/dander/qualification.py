"""Fail-closed normalized reports for Phase 8 scale qualification."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from dander.telemetry import MeasurementStatus, RunPerformance

QUALIFICATION_REPORT_SCHEMA = "io.dander.qualification.report/v1"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@#-]{0,255}$")


class BenchmarkClass(StrEnum):
    """Required benchmark classes from the cloud-portability roadmap."""

    CORRECTNESS = "correctness"
    BOUNDED_MEMORY = "bounded_memory"
    BULK_THROUGHPUT = "bulk_throughput"
    INCREMENTAL = "incremental"
    CONCURRENT_PIPELINES = "concurrent_pipelines"
    TRANSFORM = "transform"
    FAILURE = "failure"
    CROSSOVER = "crossover"
    COST = "cost"


class QualificationStatus(StrEnum):
    """Whether one report passed, failed, or lacks enough evidence to evaluate."""

    NOT_EVALUATED = "not_evaluated"
    FAILED = "failed"
    PASSED = "passed"


class ObjectiveStatus(StrEnum):
    """Result of one approved objective within a qualification report."""

    NOT_EVALUATED = "not_evaluated"
    FAILED = "failed"
    PASSED = "passed"


@dataclass(frozen=True, slots=True)
class ApprovedCostCeiling:
    """Human-approved mutation ceiling recorded before one live benchmark."""

    amount_usd: Decimal
    approval_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount_usd, Decimal) or not self.amount_usd.is_finite():
            raise ValueError("approved cost ceiling must be a finite Decimal")
        if self.amount_usd < 0:
            raise ValueError("approved cost ceiling must be non-negative")
        _require_reference(self.approval_reference, label="approval reference")

    def to_payload(self) -> dict[str, str]:
        return {
            "amount_usd": str(self.amount_usd),
            "approval_reference": self.approval_reference,
        }


@dataclass(frozen=True, slots=True)
class ApprovedObjectiveSet:
    """Human-approved objectives bound to one exact benchmark and candidate."""

    names: tuple[str, ...]
    benchmark_class: BenchmarkClass
    profile_id: str
    release_version: str
    git_commit: str
    image_digest: str
    configuration_sha256: str
    approval_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.names, tuple) or not self.names:
            raise ValueError("approved objective set requires at least one name")
        if list(self.names) != sorted(self.names) or len(self.names) != len(set(self.names)):
            raise ValueError("approved objective names must be unique and sorted")
        for name in self.names:
            _require_name(name, label="approved objective name")
        if not isinstance(self.benchmark_class, BenchmarkClass):
            raise ValueError("approved objective benchmark class must be a BenchmarkClass")
        _require_name(self.profile_id, label="approved objective profile")
        _require_reference(self.release_version, label="approved objective release version")
        if not isinstance(self.git_commit, str) or _GIT_COMMIT.fullmatch(self.git_commit) is None:
            raise ValueError("approved objective git commit must be a full lowercase SHA-1")
        if not isinstance(self.image_digest, str) or _DIGEST.fullmatch(self.image_digest) is None:
            raise ValueError("approved objective image digest must be an immutable sha256 digest")
        if (
            not isinstance(self.configuration_sha256, str)
            or _DIGEST.fullmatch(f"sha256:{self.configuration_sha256}") is None
        ):
            raise ValueError(
                "approved objective configuration_sha256 must contain 64 lowercase hex characters"
            )
        _require_reference(self.approval_reference, label="objective approval reference")

    def to_payload(self) -> dict[str, object]:
        return {
            "names": list(self.names),
            "benchmark_class": self.benchmark_class.value,
            "profile_id": self.profile_id,
            "release_version": self.release_version,
            "git_commit": self.git_commit,
            "image_digest": self.image_digest,
            "configuration_sha256": self.configuration_sha256,
            "approval_reference": self.approval_reference,
        }


@dataclass(frozen=True, slots=True)
class QualificationContext:
    """Exact candidate and provider coordinates for one sanitized report."""

    release_version: str | None
    git_commit: str | None
    image_digest: str | None
    benchmark_date: date | None
    profile_id: str
    launcher: str
    warehouse: str
    state_backend: str
    catalog: str
    secret_provider: str
    regions: tuple[str, ...]
    service_shapes: tuple[str, ...]
    provider_job_ids: tuple[str, ...]
    cost_ceiling: ApprovedCostCeiling | None

    def __post_init__(self) -> None:
        if self.release_version is not None:
            _require_reference(self.release_version, label="release version")
        if self.git_commit is not None and _GIT_COMMIT.fullmatch(self.git_commit) is None:
            raise ValueError("git commit must be a full lowercase SHA-1")
        if self.image_digest is not None and _DIGEST.fullmatch(self.image_digest) is None:
            raise ValueError("image digest must be an immutable sha256 digest")
        if self.benchmark_date is not None and not isinstance(self.benchmark_date, date):
            raise ValueError("benchmark date must be a date")
        for label, value in (
            ("profile", self.profile_id),
            ("launcher", self.launcher),
            ("warehouse", self.warehouse),
            ("state backend", self.state_backend),
            ("catalog", self.catalog),
            ("secret provider", self.secret_provider),
        ):
            _require_name(value, label=label)
        for label, values in (
            ("regions", self.regions),
            ("service shapes", self.service_shapes),
            ("provider job ids", self.provider_job_ids),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"qualification {label} must be a tuple")
            if list(values) != sorted(values) or len(values) != len(set(values)):
                raise ValueError(f"qualification {label} must be unique and sorted")
            for value in values:
                _require_reference(value, label=label)
        if self.cost_ceiling is not None and not isinstance(self.cost_ceiling, ApprovedCostCeiling):
            raise ValueError("qualification context requires an approved cost ceiling")

    @property
    def complete(self) -> bool:
        """Return true only when every pass-required context field is present."""
        return all(
            value is not None
            for value in (
                self.release_version,
                self.git_commit,
                self.image_digest,
                self.benchmark_date,
                self.cost_ceiling,
            )
        ) and all((self.regions, self.service_shapes, self.provider_job_ids))

    def to_payload(self) -> dict[str, object]:
        return {
            "release_version": self.release_version,
            "git_commit": self.git_commit,
            "image_digest": self.image_digest,
            "benchmark_date": (
                self.benchmark_date.isoformat() if self.benchmark_date is not None else None
            ),
            "profile_id": self.profile_id,
            "launcher": self.launcher,
            "warehouse": self.warehouse,
            "state_backend": self.state_backend,
            "catalog": self.catalog,
            "secret_provider": self.secret_provider,
            "regions": list(self.regions),
            "service_shapes": list(self.service_shapes),
            "provider_job_ids": list(self.provider_job_ids),
            "cost_ceiling": (
                self.cost_ceiling.to_payload() if self.cost_ceiling is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkWorkload:
    """Deterministic, non-sensitive workload dimensions for one report."""

    benchmark_class: BenchmarkClass
    input_rows: int | None
    logical_input_bytes: int | None
    row_width_bytes: int | None
    schema_depth: int | None
    source_rate_limit: str | None
    transform_complexity: str | None
    concurrency: int | None
    batch_rows: int | None
    batch_bytes: int | None
    configuration_sha256: str | None
    memory_limit_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_class, BenchmarkClass):
            raise ValueError("benchmark class must be a BenchmarkClass")
        for name in (
            "input_rows",
            "logical_input_bytes",
            "row_width_bytes",
            "schema_depth",
            "concurrency",
            "batch_rows",
            "batch_bytes",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.memory_limit_bytes is not None and (
            isinstance(self.memory_limit_bytes, bool)
            or not isinstance(self.memory_limit_bytes, int)
            or self.memory_limit_bytes <= 0
        ):
            raise ValueError("memory_limit_bytes must be a positive integer")
        if self.source_rate_limit is not None:
            _require_reference(self.source_rate_limit, label="source rate limit")
        if self.transform_complexity is not None:
            _require_reference(self.transform_complexity, label="transform complexity")
        if (
            self.configuration_sha256 is not None
            and _DIGEST.fullmatch(f"sha256:{self.configuration_sha256}") is None
        ):
            raise ValueError("configuration_sha256 must contain 64 lowercase hex characters")

    @property
    def complete(self) -> bool:
        """Return true only when every common workload dimension is present."""
        return all(
            value is not None
            for value in (
                self.input_rows,
                self.logical_input_bytes,
                self.row_width_bytes,
                self.schema_depth,
                self.source_rate_limit,
                self.transform_complexity,
                self.concurrency,
                self.batch_rows,
                self.batch_bytes,
                self.configuration_sha256,
            )
        )

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["benchmark_class"] = self.benchmark_class.value
        return payload


@dataclass(frozen=True, slots=True)
class ObjectiveResult:
    """One approved SLO assertion with a stable evidence reference."""

    name: str
    status: ObjectiveStatus
    evidence_reference: str

    def __post_init__(self) -> None:
        _require_name(self.name, label="objective name")
        if not isinstance(self.status, ObjectiveStatus):
            raise ValueError("objective status must be an ObjectiveStatus")
        _require_reference(self.evidence_reference, label="objective evidence reference")

    def to_payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence_reference": self.evidence_reference,
        }


@dataclass(frozen=True, slots=True)
class QualificationReport:
    """Normalized report that cannot claim success from partial or ambiguous evidence."""

    context: QualificationContext
    workload: BenchmarkWorkload
    performance: RunPerformance
    objectives: tuple[ObjectiveResult, ...]
    approved_objectives: ApprovedObjectiveSet | None
    status: QualificationStatus
    schema: str = QUALIFICATION_REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != QUALIFICATION_REPORT_SCHEMA:
            raise ValueError("qualification report schema is incompatible")
        if not isinstance(self.context, QualificationContext):
            raise ValueError("qualification report requires a QualificationContext")
        if not isinstance(self.workload, BenchmarkWorkload):
            raise ValueError("qualification report requires a BenchmarkWorkload")
        if not isinstance(self.performance, RunPerformance):
            raise ValueError("qualification report requires RunPerformance")
        if not isinstance(self.objectives, tuple) or not self.objectives:
            raise ValueError("qualification report requires at least one objective")
        if not all(isinstance(item, ObjectiveResult) for item in self.objectives):
            raise ValueError("qualification objectives must be ObjectiveResult values")
        names = [item.name for item in self.objectives]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("qualification objectives must be unique and sorted by name")
        if self.approved_objectives is not None and not isinstance(
            self.approved_objectives, ApprovedObjectiveSet
        ):
            raise ValueError("qualification approved objectives are invalid")
        if not isinstance(self.status, QualificationStatus):
            raise ValueError("qualification status must be a QualificationStatus")
        if self.status is QualificationStatus.PASSED:
            self._require_pass_evidence()
        elif self.status is QualificationStatus.FAILED and not any(
            item.status is ObjectiveStatus.FAILED for item in self.objectives
        ):
            raise ValueError("failed qualification requires at least one failed objective")

    def _require_pass_evidence(self) -> None:
        if not self.context.complete:
            raise ValueError(
                "passed qualification requires complete candidate and provider context"
            )
        if not self.workload.complete:
            raise ValueError("passed qualification requires every workload dimension")
        if not self.performance.complete:
            raise ValueError("passed qualification requires every common metric to be measured")
        if (
            self.approved_objectives is None
            or tuple(item.name for item in self.objectives) != self.approved_objectives.names
        ):
            raise ValueError("passed qualification requires the complete approved objective set")
        assert self.context.release_version is not None
        assert self.context.git_commit is not None
        assert self.context.image_digest is not None
        assert self.workload.configuration_sha256 is not None
        if (
            self.approved_objectives.benchmark_class is not self.workload.benchmark_class
            or self.approved_objectives.profile_id != self.context.profile_id
            or self.approved_objectives.release_version != self.context.release_version
            or self.approved_objectives.git_commit != self.context.git_commit
            or self.approved_objectives.image_digest != self.context.image_digest
            or self.approved_objectives.configuration_sha256 != self.workload.configuration_sha256
        ):
            raise ValueError(
                "passed qualification approved objectives must match the exact benchmark, "
                "profile, workload, and candidate"
            )
        if any(item.status is not ObjectiveStatus.PASSED for item in self.objectives):
            raise ValueError("passed qualification requires every objective to pass")
        if not self.performance.costs:
            raise ValueError("passed qualification requires explicit cost evidence, including zero")
        if any(cost.currency != "USD" for cost in self.performance.costs):
            raise ValueError("passed qualification cost evidence must use USD")
        if any(cost.estimated for cost in self.performance.costs):
            raise ValueError("passed qualification requires measured cost evidence")
        observed_cost = sum((cost.amount for cost in self.performance.costs), Decimal(0))
        assert self.context.cost_ceiling is not None
        if observed_cost > self.context.cost_ceiling.amount_usd:
            raise ValueError("passed qualification exceeds its approved cost ceiling")
        if self.workload.benchmark_class is BenchmarkClass.BOUNDED_MEMORY:
            self._require_bounded_memory()

    def _require_bounded_memory(self) -> None:
        memory_limit = self.workload.memory_limit_bytes
        if memory_limit is None:
            raise ValueError("bounded-memory qualification requires an enforced memory limit")
        assert self.workload.logical_input_bytes is not None
        if self.workload.logical_input_bytes < memory_limit * 10:
            raise ValueError("bounded-memory input must be at least ten times the memory limit")
        peak = self.performance.peak_rss_bytes
        if peak.status is not MeasurementStatus.MEASURED or peak.value is None:
            raise ValueError("bounded-memory qualification requires measured peak RSS")
        if peak.value > Decimal(memory_limit) * Decimal("0.8"):
            raise ValueError("bounded-memory peak RSS exceeds 80 percent of the memory limit")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "status": self.status.value,
            "context": self.context.to_payload(),
            "workload": self.workload.to_payload(),
            "performance": self.performance.to_payload(),
            "approved_objectives": (
                self.approved_objectives.to_payload()
                if self.approved_objectives is not None
                else None
            ),
            "objectives": [item.to_payload() for item in self.objectives],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), separators=(",", ":"), sort_keys=True)


def _require_name(value: str, *, label: str) -> None:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError(f"{label} must use lowercase portable name syntax")


def _require_reference(value: str, *, label: str) -> None:
    if not isinstance(value, str) or _REFERENCE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a non-secret stable reference")


__all__ = [
    "ApprovedCostCeiling",
    "ApprovedObjectiveSet",
    "BenchmarkClass",
    "BenchmarkWorkload",
    "ObjectiveResult",
    "ObjectiveStatus",
    "QUALIFICATION_REPORT_SCHEMA",
    "QualificationContext",
    "QualificationReport",
    "QualificationStatus",
]
