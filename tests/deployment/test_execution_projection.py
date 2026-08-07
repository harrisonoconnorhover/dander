"""Cloud-neutral execution projection and fail-closed launcher limits."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from dander.deployment import (
    CLOUD_RUN_CAPABILITIES,
    ExecutionProjectionError,
    ExecutionTemplate,
    ResourceProjection,
    build_gcp_v1_execution_templates,
    validate_launcher_projection,
)
from dander.project import load_project_config
from dander.runtime_contract import RUNTIME_CONTRACT

_IMAGE = "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64


def _template(root: Path) -> ExecutionTemplate:
    manifest = load_project_config(root / "dander.yaml")
    return build_gcp_v1_execution_templates(
        manifest,
        image=_IMAGE,
        project="unit-project",
        alert_target="operator@example.invalid",
    )["greenhouse_jobs"]


def test_v1_gcp_projection_preserves_current_hosted_intent() -> None:
    template = _template(Path(__file__).parents[2])

    assert template.schema == "io.dander.execution/v1"
    assert template.contract == RUNTIME_CONTRACT
    assert template.launcher == "cloud_run"
    assert template.image == _IMAGE
    assert template.command[:8] == (
        "runtime",
        "execute",
        "--contract",
        RUNTIME_CONTRACT,
        "--pipeline",
        "greenhouse_jobs",
        "--platform",
        "gcp",
    )
    assert template.resources.cpu_millis == 1_000
    assert template.resources.memory_mib == 512
    assert template.resources.runtime_retry_count == 0
    assert template.resources.launcher_retry_count == 1
    assert template.schedule.task_count == 1
    assert template.schedule.maximum_parallelism == 1
    assert dict(template.environment)["DANDER_IMAGE_DIGEST"] == "sha256:" + "a" * 64
    assert template.secret_bindings == ()
    observability = template.as_dict()["observability"]
    assert isinstance(observability, dict)
    assert observability["log_destination"] == "cloud_logging"


def test_projection_contains_secret_references_but_no_values(tmp_path: Path) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        """\
version: 1
platform:
  safety:
    require_guarded_free_tier: false
pipelines:
  salesforce_crm:
    source: salesforce
    models: []
    build_models: false
    secrets:
      SALESFORCE_KEY: salesforce-private-key
""",
        encoding="utf-8",
    )

    template = build_gcp_v1_execution_templates(
        load_project_config(config), image=_IMAGE, project="unit-project"
    )["salesforce_crm"]

    reference = dict(template.secret_bindings)["SALESFORCE_KEY"]
    assert reference.reference == (
        "gcp-sm://projects/unit-project/secrets/salesforce-private-key/versions/latest"
    )
    assert "SALESFORCE_KEY" not in dict(template.environment)


def test_run_binding_projects_validated_correlation_environment() -> None:
    request = _template(Path(__file__).parents[2]).bind(
        run_id="run-123",
        launcher_execution_id="execution-456",
        attempt=2,
        deadline_at="2026-08-07T09:00:00Z",
    )

    environment = request.environment()
    assert environment["DANDER_RUN_ID"] == "run-123"
    assert environment["DANDER_LAUNCHER_EXECUTION_ID"] == "execution-456"
    assert environment["DANDER_ATTEMPT"] == "2"
    assert environment["DANDER_SHARD_INDEX"] == "0"
    assert environment["DANDER_SHARD_COUNT"] == "1"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cpu_millis", 3_000, "CPU"),
        ("memory_mib", 64, "memory"),
        ("deadline_seconds", 86_401, "deadline"),
        ("launcher_retry_count", 11, "retry"),
        ("ephemeral_storage_mib", 1_024, "ephemeral"),
    ],
)
def test_cloud_run_rejects_unsupported_projection_fields(
    field: str,
    value: int,
    message: str,
) -> None:
    template = _template(Path(__file__).parents[2])
    resources = replace(template.resources, **{field: value})

    with pytest.raises(ExecutionProjectionError, match=message):
        validate_launcher_projection(replace(template, resources=resources), CLOUD_RUN_CAPABILITIES)


def test_projection_rejects_mutable_image_reference() -> None:
    root = Path(__file__).parents[2]

    with pytest.raises(ExecutionProjectionError, match="immutable"):
        build_gcp_v1_execution_templates(
            load_project_config(root / "dander.yaml"),
            image="us-central1-docker.pkg.dev/unit-project/dander/dander:latest",
            project="unit-project",
        )


def test_resource_projection_rejects_negative_runtime_retries() -> None:
    with pytest.raises(ExecutionProjectionError, match="must not be negative"):
        ResourceProjection(
            cpu_millis=1_000,
            memory_mib=512,
            ephemeral_storage_mib=None,
            deadline_seconds=300,
            runtime_retry_count=-1,
            launcher_retry_count=0,
        )
