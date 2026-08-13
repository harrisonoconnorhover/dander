"""Credential-free tests for the bounded GCS GraphStore live runner."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts.portability.gcs_graph_store_qualification import (
    BucketPolicyEvidence,
    QualificationError,
    build_evidence,
    inspect_bucket_policy,
    load_fixture,
    run_graph_workflow,
    validate_approval,
)
from tests.control.gcs_fakes import (
    FakeGCSBackend,
    FakeGCSClient,
    FakeNotFoundError,
    FakePreconditionError,
)

from dander.control import GCSGraphStore


def _store(backend: FakeGCSBackend, *, prefix: str = "qualification/v1") -> GCSGraphStore:
    return GCSGraphStore(
        "unit-bucket",
        prefix=prefix,
        client=FakeGCSClient(backend),
        not_found_errors=(FakeNotFoundError,),
        precondition_errors=(FakePreconditionError,),
    )


def _policy_bucket(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "location": "US-CENTRAL1",
        "storage_class": "STANDARD",
        "versioning_enabled": True,
        "default_kms_key_name": None,
        "soft_delete_policy": SimpleNamespace(retention_duration_seconds=0),
        "iam_configuration": SimpleNamespace(
            uniform_bucket_level_access_enabled=True,
            public_access_prevention="enforced",
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_live_workflow_uses_the_real_adapter_and_converges_across_restarts() -> None:
    backend = FakeGCSBackend()

    result = run_graph_workflow(
        lambda: _store(backend),
        bucket_name="unit-bucket",
        prefix="qualification/v1",
        document=load_fixture(),
    )

    assert all(value for value in asdict(result).values() if isinstance(value, bool))
    assert len(result.fixture_content_sha256) == 64
    assert result.fixture_canonical_bytes > 0
    assert not any(name.endswith("/graphs/canonical_graph.json") for name in backend.objects)


def test_bucket_policy_fails_closed_on_any_missing_requirement() -> None:
    accepted = inspect_bucket_policy(cast("Any", _policy_bucket()), expected_location="us-central1")
    assert all(asdict(accepted).values())

    with pytest.raises(QualificationError, match="policy"):
        inspect_bucket_policy(
            cast("Any", _policy_bucket(versioning_enabled=False)),
            expected_location="us-central1",
        )


def test_evidence_is_sanitized_and_marks_post_run_gates_pending() -> None:
    backend = FakeGCSBackend()
    private_prefix = "private-coordinate-prefix/v1"
    workflow = run_graph_workflow(
        lambda: _store(backend, prefix=private_prefix),
        bucket_name="unit-bucket",
        prefix=private_prefix,
        document=load_fixture(),
    )
    policy = BucketPolicyEvidence(True, True, True, True, True, True, True)

    evidence = build_evidence(
        implementation_commit="a" * 40,
        approval_reference="druff-d3-gcs-live-2026-08-13-attempt-1",
        approved_cost_ceiling_usd="0.25",
        bucket_policy=policy,
        workflow=workflow,
        recorded_at=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
    )
    encoded = json.dumps(evidence, sort_keys=True)

    assert "unit-bucket" not in encoded
    assert private_prefix not in encoded
    assert "revision" not in encoded
    assert "document" not in encoded
    assert evidence["cleanup"] == {
        "all_object_versions_removed": False,
        "bucket_removed": False,
        "retained_stage_zero_no_drift": False,
        "retained_platform_no_drift": False,
    }


@pytest.mark.parametrize("ceiling", ("0", "-1", "nan", "inf", "not-money"))
def test_approval_inputs_reject_invalid_cost_ceilings(ceiling: str) -> None:
    with pytest.raises(QualificationError, match="ceiling"):
        validate_approval(ceiling, "approval-ref", "a" * 40)


def test_approval_inputs_preserve_exact_decimal_ceiling() -> None:
    assert validate_approval("0.25", "approval-ref", "a" * 40) == str(Decimal("0.25"))
