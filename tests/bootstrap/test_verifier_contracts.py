"""Contract tests for resource-scoped IAM and cost-guard verification."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dander.bootstrap import DeploymentVerifier, VerificationStatus

if TYPE_CHECKING:
    from pathlib import Path


def _verifier(tmp_path: Path, payloads: dict[tuple[str, ...], object]) -> DeploymentVerifier:
    def run(args: tuple[str, ...], cwd: Path) -> str:
        assert cwd == tmp_path.resolve()
        return json.dumps(payloads.get(args, {}))

    return DeploymentVerifier(project="proof-project", infra_dir=tmp_path, command_runner=run)


def test_runtime_project_iam_distinguishes_missing_and_broad_roles(tmp_path: Path) -> None:
    missing = _verifier(tmp_path, {})._check_runtime_iam(
        "dander-runtime@proof-project.iam.gserviceaccount.com", publish_dataplex=False
    )
    assert not missing.ok
    assert missing.status is VerificationStatus.MISSING_REQUIRED_BINDING
    assert missing.detail == "required project role missing"

    payload: dict[tuple[str, ...], object] = {
        (
            "gcloud",
            "projects",
            "get-iam-policy",
            "proof-project",
            "--format=json",
        ): {
            "bindings": [
                {
                    "role": "roles/bigquery.jobUser",
                    "members": [
                        "serviceAccount:dander-runtime@proof-project.iam.gserviceaccount.com"
                    ],
                },
                {
                    "role": "roles/pubsub.viewer",
                    "members": [
                        "serviceAccount:dander-runtime@proof-project.iam.gserviceaccount.com"
                    ],
                },
                {
                    "role": "roles/billing.viewer",
                    "members": [
                        "serviceAccount:dander-runtime@proof-project.iam.gserviceaccount.com"
                    ],
                },
                {
                    "role": "roles/storage.admin",
                    "members": [
                        "serviceAccount:dander-runtime@proof-project.iam.gserviceaccount.com"
                    ],
                },
            ]
        }
    }
    broad = _verifier(tmp_path, payload)._check_runtime_iam(
        "dander-runtime@proof-project.iam.gserviceaccount.com", publish_dataplex=False
    )
    assert not broad.ok
    assert broad.status is VerificationStatus.BROAD_BINDING_DETECTED


def test_unguarded_runtime_iam_rejects_guard_only_project_roles(tmp_path: Path) -> None:
    service_account = "dander-runtime@proof-project.iam.gserviceaccount.com"
    command = (
        "gcloud",
        "projects",
        "get-iam-policy",
        "proof-project",
        "--format=json",
    )
    ordinary = {
        "bindings": [
            {
                "role": "roles/bigquery.jobUser",
                "members": [f"serviceAccount:{service_account}"],
            }
        ]
    }
    assert (
        _verifier(tmp_path, {command: ordinary})
        ._check_runtime_iam(service_account, publish_dataplex=False)
        .ok
    )

    guarded_role = {
        "bindings": [
            *ordinary["bindings"],
            {
                "role": "roles/pubsub.viewer",
                "members": [f"serviceAccount:{service_account}"],
            },
        ]
    }
    unexpected = _verifier(tmp_path, {command: guarded_role})._check_runtime_iam(
        service_account, publish_dataplex=False
    )
    assert not unexpected.ok
    assert unexpected.status is VerificationStatus.UNEXPECTED_BINDING


def test_guarded_runtime_iam_requires_pubsub_viewer(tmp_path: Path) -> None:
    service_account = "dander-runtime@proof-project.iam.gserviceaccount.com"
    command = (
        "gcloud",
        "projects",
        "get-iam-policy",
        "proof-project",
        "--format=json",
    )
    payload = {
        "bindings": [
            {
                "role": role,
                "members": [f"serviceAccount:{service_account}"],
            }
            for role in ("roles/bigquery.jobUser", "roles/pubsub.viewer")
        ]
    }
    assert (
        _verifier(tmp_path, {command: payload})
        ._check_runtime_iam(
            service_account,
            publish_dataplex=False,
            billing_account_id="ABCDEF-123456-ABCDEF",
        )
        .ok
    )


def test_runtime_billing_iam_is_checked_at_billing_account_scope(tmp_path: Path) -> None:
    service_account = "dander-runtime@proof-project.iam.gserviceaccount.com"
    payloads: dict[tuple[str, ...], object] = {
        (
            "gcloud",
            "billing",
            "accounts",
            "get-iam-policy",
            "ABCDEF-123456-ABCDEF",
            "--format=json",
        ): {
            "bindings": [
                {
                    "role": "roles/billing.viewer",
                    "members": [f"serviceAccount:{service_account}"],
                }
            ]
        }
    }
    check = _verifier(tmp_path, payloads)._check_runtime_billing_iam(
        service_account, billing_account_id="ABCDEF-123456-ABCDEF"
    )
    assert check.ok
    assert check.status is VerificationStatus.VERIFIED


def test_empty_runtime_secret_scope_does_not_require_secret_manager_inventory(
    tmp_path: Path,
) -> None:
    check = _verifier(tmp_path, {})._check_runtime_secret_scope(
        "dander-runtime@proof-project.iam.gserviceaccount.com", ()
    )[0]
    assert check.ok


def test_runtime_job_accepts_gcloud_v1_shape(tmp_path: Path) -> None:
    image = "us-central1-docker.pkg.dev/proof-project/dander/dander@sha256:" + "a" * 64
    payloads: dict[tuple[str, ...], object] = {
        (
            "gcloud",
            "run",
            "jobs",
            "describe",
            "dander-greenhouse-public",
            "--project",
            "proof-project",
            "--region",
            "us-central1",
            "--format=json",
        ): {
            "spec": {
                "template": {
                    "spec": {
                        "template": {
                            "spec": {
                                "serviceAccountName": (
                                    "dander-runtime@proof-project.iam.gserviceaccount.com"
                                ),
                                "containers": [{"image": image}],
                            }
                        }
                    }
                }
            }
        }
    }
    check, service_account, observed_image = _verifier(tmp_path, payloads)._check_runtime_job(
        "dander-greenhouse-public", "us-central1", image
    )
    assert check.ok
    assert service_account == "dander-runtime@proof-project.iam.gserviceaccount.com"
    assert observed_image == image


def test_cost_guard_requires_exact_resources(tmp_path: Path) -> None:
    payloads: dict[tuple[str, ...], object] = {
        (
            "gcloud",
            "billing",
            "budgets",
            "list",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
            "--format=json",
        ): [
            {
                "displayName": "dander-sbx-cap",
                "amount": {"specifiedAmount": {"units": "5", "nanos": 0}},
                "budgetFilter": {"projects": ["projects/123"]},
                "thresholdRules": [
                    {"thresholdPercent": 0.8},
                    {"thresholdPercent": 1.0},
                ],
                "notificationsRule": {
                    "pubsubTopic": "projects/proof-project/topics/dander-stop-billing"
                },
            }
        ],
        ("gcloud", "projects", "describe", "proof-project", "--format=json"): {
            "projectNumber": "123"
        },
        (
            "gcloud",
            "pubsub",
            "topics",
            "describe",
            "dander-stop-billing",
            "--project",
            "proof-project",
            "--format=json",
        ): {"name": "projects/proof-project/topics/dander-stop-billing"},
        (
            "gcloud",
            "functions",
            "describe",
            "dander-stop-billing",
            "--gen2",
            "--region",
            "us-central1",
            "--project",
            "proof-project",
            "--format=json",
        ): {
            "serviceConfig": {
                "serviceAccountEmail": "dander-cost-guard@proof-project.iam.gserviceaccount.com",
                "environmentVariables": {"SIMULATE_DEACTIVATION": "true"},
            },
            "eventTrigger": {"pubsubTopic": "projects/proof-project/topics/dander-stop-billing"},
        },
        (
            "gcloud",
            "billing",
            "projects",
            "describe",
            "proof-project",
            "--format=json",
        ): {"billingAccountName": "billingAccounts/ABCDEF-123456-ABCDEF"},
    }

    checks = _verifier(tmp_path, payloads)._check_cost_guard(
        "ABCDEF-123456-ABCDEF",
        budget_name="dander-sbx-cap",
        amount=5.0,
        topic="dander-stop-billing",
        function_name="dander-stop-billing",
        simulate=True,
        region="us-central1",
    )

    assert all(check.ok for check in checks)
    assert {check.name for check in checks} == {
        "cost_guard:budget",
        "cost_guard:topic",
        "cost_guard:function",
        "cost_guard:billing_link",
    }
