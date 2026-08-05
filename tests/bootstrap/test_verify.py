"""Read-only deployment verification and evidence tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dander.bootstrap import DeploymentVerifier, write_summary

if TYPE_CHECKING:
    from pathlib import Path


class FakeBigQueryClient:
    def __init__(self, datasets: set[str]) -> None:
        self.datasets = datasets

    def get_dataset(self, dataset_ref: str) -> object:
        if dataset_ref.rsplit(".", 1)[-1] not in self.datasets:
            raise LookupError(dataset_ref)
        return object()


def test_verifier_checks_actual_resources_and_writes_sanitized_summary(tmp_path: Path) -> None:
    metadata_dir = tmp_path / ".terraform"
    metadata_dir.mkdir()
    (metadata_dir / "terraform.tfstate").write_text(
        json.dumps(
            {
                "backend": {
                    "type": "gcs",
                    "config": {"bucket": "proof-state", "prefix": "dander/state"},
                }
            }
        ),
        encoding="utf-8",
    )
    command_payloads = {
        ("gcloud", "projects", "describe", "proof-project", "--format=json"): {
            "projectId": "proof-project",
            "lifecycleState": "ACTIVE",
        },
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
            "template": {
                "template": {
                    "serviceAccount": "dander-runtime@proof-project.iam.gserviceaccount.com",
                    "containers": [{"image": "example.invalid/dander@sha256:" + "a" * 64}],
                }
            }
        },
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
            ]
        },
        (
            "gcloud",
            "iam",
            "service-accounts",
            "get-iam-policy",
            "dander-runtime@proof-project.iam.gserviceaccount.com",
            "--format=json",
        ): {"bindings": []},
        (
            "gcloud",
            "scheduler",
            "jobs",
            "describe",
            "dander-greenhouse-public-daily",
            "--project",
            "proof-project",
            "--location",
            "us-central1",
            "--format=json",
        ): {
            "name": (
                "projects/proof-project/locations/us-central1/jobs/dander-greenhouse-public-daily"
            ),
            "state": "PAUSED",
        },
        (
            "gcloud",
            "secrets",
            "list",
            "--project",
            "proof-project",
            "--format=json(name)",
        ): [{"name": "projects/proof-project/secrets/hubspot-private-app-token"}],
        (
            "gcloud",
            "secrets",
            "get-iam-policy",
            "hubspot-private-app-token",
            "--project",
            "proof-project",
            "--format=json",
        ): {
            "bindings": [
                {
                    "role": "roles/secretmanager.secretAccessor",
                    "members": [
                        "serviceAccount:dander-runtime@proof-project.iam.gserviceaccount.com"
                    ],
                }
            ]
        },
        (
            "gcloud",
            "secrets",
            "describe",
            "hubspot-private-app-token",
            "--project",
            "proof-project",
            "--format=json(name)",
        ): {"name": "projects/proof-project/secrets/hubspot-private-app-token"},
    }

    for dataset in ("raw", "staging", "marts", "dander_meta"):
        command_payloads[("bq", "show", "--format=json", f"proof-project:{dataset}")] = {
            "access": [
                {
                    "role": "WRITER",
                    "userByEmail": "dander-runtime@proof-project.iam.gserviceaccount.com",
                }
            ]
        }

    def run(args: tuple[str, ...], cwd: Path) -> str:
        assert cwd == tmp_path.resolve()
        if args == ("terraform", "state", "pull"):
            return "{}"
        return json.dumps(command_payloads[args])

    summary = DeploymentVerifier(
        project="proof-project",
        infra_dir=tmp_path,
        command_runner=run,
        bigquery_client_factory=lambda _project: FakeBigQueryClient(
            {"raw", "staging", "marts", "dander_meta"}
        ),
    ).verify(
        state_bucket="proof-state",
        state_prefix="dander/state",
        runtime_job="dander-greenhouse-public",
        scheduler_job="dander-greenhouse-public-daily",
        secret_ids=("hubspot-private-app-token",),
        runtime_image="example.invalid/dander@sha256:" + "a" * 64,
    )

    assert summary.passed
    output = tmp_path / "evidence" / "bootstrap-summary.json"
    write_summary(summary, output)
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["passed"] is True
    assert all("payload" not in json.dumps(check) for check in evidence["checks"])


def test_verifier_retains_failed_checks_without_claiming_success(tmp_path: Path) -> None:
    metadata_dir = tmp_path / ".terraform"
    metadata_dir.mkdir()
    (metadata_dir / "terraform.tfstate").write_text(
        json.dumps({"backend": {"type": "local", "config": {}}}),
        encoding="utf-8",
    )

    summary = DeploymentVerifier(
        project="proof-project",
        infra_dir=tmp_path,
        command_runner=lambda _args, _cwd: json.dumps({}),
        bigquery_client_factory=lambda _project: FakeBigQueryClient(set()),
    ).verify(datasets=("raw",))

    assert not summary.passed
    assert any(not check.ok for check in summary.checks)
    assert any(check.name == "remote_state" for check in summary.checks)
