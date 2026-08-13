"""Run one sanitized, bounded live qualification of the GCS GraphStore adapter."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from dander.control import (
    GCSGraphStore,
    GraphStore,
    GraphStoreConflictError,
    GraphStoreNotFoundError,
    canonicalize_graph_document,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload

if TYPE_CHECKING:
    from collections.abc import Callable

_APPROVAL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SCHEMA = "io.dander.druff.gcs-graph-store-qualification/v1"
_PROJECT = "qualification"
_GRAPH = "canonical_graph"
_CREATE_KEY = "gcs-live-create-0001"
_DELETE_KEY = "gcs-live-delete-0001"


class QualificationError(RuntimeError):
    """Raised with a sanitized message when the bounded qualification fails."""


class _SoftDeletePolicyPort(Protocol):
    retention_duration_seconds: int | None


class _IamConfigurationPort(Protocol):
    uniform_bucket_level_access_enabled: bool
    public_access_prevention: str


class _BucketPort(Protocol):
    location: str | None
    storage_class: str | None
    versioning_enabled: bool
    default_kms_key_name: str | None
    soft_delete_policy: _SoftDeletePolicyPort
    iam_configuration: _IamConfigurationPort


@dataclass(frozen=True, slots=True)
class BucketPolicyEvidence:
    """Safe booleans describing the disposable qualification bucket."""

    expected_location: bool
    standard_storage: bool
    uniform_bucket_access: bool
    public_access_prevention: bool
    versioning: bool
    provider_default_encryption: bool
    soft_delete_disabled: bool


@dataclass(frozen=True, slots=True)
class WorkflowEvidence:
    """Provider-neutral outcomes from the live GraphStore workflow."""

    bucket_binding: bool
    create_read_equal: bool
    create_replay_exact_after_restart: bool
    create_replay_exact_after_update: bool
    list_summary_equal: bool
    restart_persistence_after_update: bool
    stale_update_rejected: bool
    stale_delete_rejected: bool
    delete_replay_exact_after_restart: bool
    absent_after_delete: bool
    empty_list_after_delete: bool
    fixture_content_sha256: str
    fixture_canonical_bytes: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)


def validate_approval(ceiling: str, reference: str, commit: str) -> str:
    """Validate and normalize the human-approved, non-secret proof inputs."""
    try:
        amount = Decimal(ceiling)
    except InvalidOperation as error:
        raise QualificationError("The approved cost ceiling is invalid.") from error
    if not amount.is_finite() or amount <= 0:
        raise QualificationError("The approved cost ceiling must be finite and positive.")
    if _APPROVAL_REFERENCE.fullmatch(reference) is None:
        raise QualificationError("The approval reference is invalid.")
    if _COMMIT.fullmatch(commit) is None:
        raise QualificationError("The implementation commit is invalid.")
    return format(amount, "f")


def inspect_bucket_policy(
    bucket: _BucketPort,
    *,
    expected_location: str,
) -> BucketPolicyEvidence:
    """Fail closed unless the disposable bucket has the reviewed policy."""
    location = (bucket.location or "").upper()
    storage_class = (bucket.storage_class or "").upper()
    retention = bucket.soft_delete_policy.retention_duration_seconds
    evidence = BucketPolicyEvidence(
        expected_location=location == expected_location.upper(),
        standard_storage=storage_class == "STANDARD",
        uniform_bucket_access=bool(bucket.iam_configuration.uniform_bucket_level_access_enabled),
        public_access_prevention=(bucket.iam_configuration.public_access_prevention == "enforced"),
        versioning=bool(bucket.versioning_enabled),
        provider_default_encryption=bucket.default_kms_key_name is None,
        soft_delete_disabled=retention in (None, 0),
    )
    _require(all(asdict(evidence).values()), "The qualification bucket policy is incomplete.")
    return evidence


def load_fixture() -> PipelineGraphDocument:
    """Load the deterministic packaged Control-contract graph fixture."""
    path = PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PipelineGraphDocument.model_validate(payload)


def run_graph_workflow(
    store_factory: Callable[[], GraphStore],
    *,
    bucket_name: str,
    prefix: str,
    document: PipelineGraphDocument,
) -> WorkflowEvidence:
    """Exercise restart, conflict, replay, pagination, and deletion semantics."""
    canonical = canonicalize_graph_document(document)
    first = store_factory()
    _require(
        isinstance(first, GCSGraphStore)
        and first.bucket_name == bucket_name
        and first.prefix == prefix,
        "The GCS GraphStore binding is not exact.",
    )
    created = first.create(_PROJECT, _GRAPH, document, idempotency_key=_CREATE_KEY)
    create_read_equal = first.get(_PROJECT, _GRAPH) == created

    restarted = store_factory()
    persisted_after_restart = restarted.get(_PROJECT, _GRAPH)
    replayed = restarted.create(
        _PROJECT,
        _GRAPH,
        document,
        idempotency_key=_CREATE_KEY,
    )
    page = restarted.list(_PROJECT, limit=1)

    changed_payload = graph_to_payload(document.to_domain())
    changed_payload["name"] = "gcs_live_qualification_updated"
    changed = PipelineGraphDocument.model_validate(changed_payload)
    updated = restarted.put(
        _PROJECT,
        _GRAPH,
        changed,
        expected_revision=created.revision,
    )

    stale_update_rejected = False
    try:
        restarted.put(
            _PROJECT,
            _GRAPH,
            document,
            expected_revision=created.revision,
        )
    except GraphStoreConflictError:
        stale_update_rejected = True

    stale_delete_rejected = False
    try:
        restarted.delete(
            _PROJECT,
            _GRAPH,
            expected_revision=created.revision,
            idempotency_key=_DELETE_KEY,
        )
    except GraphStoreConflictError:
        stale_delete_rejected = True

    restarted_again = store_factory()
    persisted_update = restarted_again.get(_PROJECT, _GRAPH)
    replay_after_update = restarted_again.create(
        _PROJECT,
        _GRAPH,
        document,
        idempotency_key=_CREATE_KEY,
    )
    receipt = restarted_again.delete(
        _PROJECT,
        _GRAPH,
        expected_revision=updated.revision,
        idempotency_key=_DELETE_KEY,
    )
    final_store = store_factory()
    replayed_receipt = final_store.delete(
        _PROJECT,
        _GRAPH,
        expected_revision=updated.revision,
        idempotency_key=_DELETE_KEY,
    )
    absent_after_delete = False
    try:
        final_store.get(_PROJECT, _GRAPH)
    except GraphStoreNotFoundError:
        absent_after_delete = True

    evidence = WorkflowEvidence(
        bucket_binding=True,
        create_read_equal=create_read_equal,
        create_replay_exact_after_restart=(
            persisted_after_restart == created and replayed == created
        ),
        create_replay_exact_after_update=replay_after_update == created,
        list_summary_equal=(page.items == (created.summary(),) and page.next_cursor is None),
        restart_persistence_after_update=persisted_update == updated,
        stale_update_rejected=stale_update_rejected,
        stale_delete_rejected=stale_delete_rejected,
        delete_replay_exact_after_restart=replayed_receipt == receipt,
        absent_after_delete=absent_after_delete,
        empty_list_after_delete=final_store.list(_PROJECT).items == (),
        fixture_content_sha256=canonical.content_sha256,
        fixture_canonical_bytes=len(canonical.data),
    )
    boolean_results = {
        key: value for key, value in asdict(evidence).items() if isinstance(value, bool)
    }
    _require(all(boolean_results.values()), "The GCS GraphStore workflow did not converge.")
    return evidence


def build_evidence(
    *,
    implementation_commit: str,
    approval_reference: str,
    approved_cost_ceiling_usd: str,
    bucket_policy: BucketPolicyEvidence,
    workflow: WorkflowEvidence,
    recorded_at: datetime | None = None,
) -> dict[str, object]:
    """Build the deliberately coordinate-free live evidence payload."""
    timestamp = recorded_at or datetime.now(UTC)
    _require(timestamp.tzinfo is not None, "The evidence timestamp must include a timezone.")
    return {
        "schema": _SCHEMA,
        "recorded_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "provider": "gcp",
        "implementation": {
            "protected_main_commit": implementation_commit,
            "scope": "protected_main_source",
            "public_distribution_qualified": False,
        },
        "approval": {
            "reference": approval_reference,
            "cost_ceiling_usd": approved_cost_ceiling_usd,
            "automatic_paid_rerun": False,
        },
        "bucket_policy": asdict(bucket_policy),
        "graph_store": asdict(workflow),
        "cleanup": {
            "all_object_versions_removed": False,
            "bucket_removed": False,
            "retained_stage_zero_no_drift": False,
            "retained_platform_no_drift": False,
        },
    }


def write_evidence(path: Path, evidence: dict[str, object]) -> None:
    """Write one private local evidence candidate without following a symlink."""
    if path.is_symlink():
        raise QualificationError("The evidence output must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--expected-location", default="us-central1")
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--approved-cost-ceiling-usd", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--evidence-output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one live proof and emit only a fixed safe failure on provider errors."""
    args = _parser().parse_args(argv)
    try:
        ceiling = validate_approval(
            args.approved_cost_ceiling_usd,
            args.approval_reference,
            args.implementation_commit,
        )
        from google.cloud.storage import Client  # type: ignore[import-untyped]

        client = Client()
        bucket = client.get_bucket(args.bucket)
        policy = inspect_bucket_policy(bucket, expected_location=args.expected_location)
        workflow = run_graph_workflow(
            lambda: GCSGraphStore(args.bucket, prefix=args.prefix),
            bucket_name=args.bucket,
            prefix=args.prefix,
            document=load_fixture(),
        )
        evidence = build_evidence(
            implementation_commit=args.implementation_commit,
            approval_reference=args.approval_reference,
            approved_cost_ceiling_usd=ceiling,
            bucket_policy=policy,
            workflow=workflow,
        )
        write_evidence(args.evidence_output, evidence)
    except Exception:
        # Provider exceptions can carry account coordinates. Never print their messages.
        print("GCS GraphStore qualification failed safely.", file=sys.stderr)
        return 1
    print("GCS GraphStore qualification passed; sanitized evidence was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
