"""Launcher-provider selection and projection parity coverage."""

from __future__ import annotations

import sys

import pytest

from dander.deployment import (
    ExecutionProjectionError,
    LauncherRuntime,
    build_gcp_execution_templates,
)
from dander.providers import ProviderKind, default_provider_registry

_IMAGE = "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64
_FARGATE_IMAGE = "184463061564.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "b" * 64
_PIPELINES: dict[str, dict[str, object]] = {
    "greenhouse_jobs": {
        "runtime_service_account_id": "dander-runtime",
        "build_models": True,
        "schedule": "0 9 * * *",
        "time_zone": "America/New_York",
        "paused": True,
        "secret_env": {},
    }
}


def _templates(runtime: LauncherRuntime) -> dict[str, object]:
    return {
        pipeline_id: template.as_dict()
        for pipeline_id, template in runtime.templates.build(
            _PIPELINES,
            image=_IMAGE,
            project="unit-project",
            cpu=1,
            memory="512Mi",
            deadline_seconds=300,
            launcher_retry_count=1,
            batch_rows=10_000,
            require_guarded_free_tier=False,
            alert_target="operator@example.invalid",
        ).items()
    }


def test_cloud_run_factory_is_lazy_and_matches_the_accepted_projection() -> None:
    module_name = "dander.providers.cloud_run.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {"provider": "cloud_run", "region": "us-central1"},
    )

    assert module_name not in sys.modules
    runtime = registry.build(ProviderKind.LAUNCHER, config)

    assert isinstance(runtime, LauncherRuntime)
    assert runtime.provider_id == "cloud_run"
    assert runtime.region == "us-central1"
    assert runtime.capabilities.supports_schedules is True
    assert module_name in sys.modules
    expected = {
        pipeline_id: template.as_dict()
        for pipeline_id, template in build_gcp_execution_templates(
            _PIPELINES,
            image=_IMAGE,
            project="unit-project",
            cpu=1,
            memory="512Mi",
            deadline_seconds=300,
            launcher_retry_count=1,
            batch_rows=10_000,
            require_guarded_free_tier=False,
            alert_target="operator@example.invalid",
        ).items()
    }
    assert _templates(runtime) == expected


def _fargate_runtime() -> LauncherRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {
            "provider": "fargate",
            "region": "us-east-1",
            "aws_account_id": "184463061564",
            "google_workload_identity_audience": (
                "//iam.googleapis.com/projects/1009770943166/locations/global/"
                "workloadIdentityPools/dander-phase1b-aws/providers/fargate"
            ),
            "subnet_ids": ["subnet-0123456789abcdef0"],
            "security_group_ids": ["sg-0123456789abcdef0"],
            "architecture": "ARM64",
            "assign_public_ip": True,
        },
    )
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def test_fargate_factory_is_lazy_and_projects_bigquery_without_credentials() -> None:
    module_name = "dander.providers.fargate.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {
            "provider": "fargate",
            "region": "us-east-1",
            "aws_account_id": "184463061564",
            "google_workload_identity_audience": (
                "//iam.googleapis.com/projects/1009770943166/locations/global/"
                "workloadIdentityPools/dander-phase1b-aws/providers/fargate"
            ),
            "subnet_ids": ["subnet-0123456789abcdef0"],
            "security_group_ids": ["sg-0123456789abcdef0"],
        },
    )

    assert module_name not in sys.modules
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    assert module_name in sys.modules
    pipelines = {
        "salesforce_crm": {
            **_PIPELINES["greenhouse_jobs"],
            "runtime_service_account_id": "dander-runtime-salesforce",
            "secret_env": {"SALESFORCE_KEY": "salesforce-private-key"},
        }
    }
    template = runtime.templates.build(
        pipelines,
        image=_FARGATE_IMAGE,
        project="unit-project",
        cpu=1,
        memory="2Gi",
        deadline_seconds=900,
        launcher_retry_count=1,
        batch_rows=1_000,
        require_guarded_free_tier=False,
        alert_target="arn:aws:sns:us-east-1:184463061564:dander-failures",
    )["salesforce_crm"]

    assert template.launcher == "fargate"
    assert template.workload_identity == (
        "arn:aws:iam::184463061564:role/dander-runtime-salesforce"
    )
    assert template.resources.ephemeral_storage_mib == 20_480
    assert dict(template.environment)["DANDER_GCP_SERVICE_ACCOUNT"] == (
        "dander-runtime-salesforce@unit-project.iam.gserviceaccount.com"
    )
    assert template.network.placement == "awsvpc"
    assert dict(template.network.extensions) == {
        "fargate_security_group_ids": "sg-0123456789abcdef0",
        "fargate_subnet_ids": "subnet-0123456789abcdef0",
    }
    assert dict(template.extensions) == {
        "fargate_architecture": "ARM64",
        "fargate_assign_public_ip": "disabled",
        "fargate_stop_timeout_seconds": "120",
    }
    assert template.schedule.expression == "cron(0 9 * * ? *)"
    secret = dict(template.secret_bindings)["SALESFORCE_KEY"]
    assert secret.reference.endswith("/secrets/salesforce-private-key/versions/latest")
    serialized = repr(template.as_dict())
    assert "PRIVATE KEY" not in serialized
    assert "AWS_SECRET_ACCESS_KEY" not in serialized


@pytest.mark.parametrize(
    ("memory", "guarded", "message"),
    [
        ("512Mi", False, "CPU/memory"),
        ("2Gi", True, "guarded-free-tier"),
    ],
)
def test_fargate_rejects_unhonored_runtime_intent(
    memory: str,
    guarded: bool,
    message: str,
) -> None:
    with pytest.raises(ExecutionProjectionError, match=message):
        _fargate_runtime().templates.build(
            _PIPELINES,
            image=_FARGATE_IMAGE,
            project="unit-project",
            cpu=1,
            memory=memory,
            deadline_seconds=900,
            launcher_retry_count=1,
            batch_rows=1_000,
            require_guarded_free_tier=guarded,
            alert_target=None,
        )


def test_fargate_accepts_a_run_longer_than_one_task_role_session() -> None:
    template = _fargate_runtime().templates.build(
        _PIPELINES,
        image=_FARGATE_IMAGE,
        project="unit-project",
        cpu=1,
        memory="2Gi",
        deadline_seconds=3_601,
        launcher_retry_count=1,
        batch_rows=1_000,
        require_guarded_free_tier=False,
        alert_target=None,
    )["greenhouse_jobs"]

    assert template.resources.deadline_seconds == 3_601


def test_fargate_rejects_a_run_longer_than_one_day() -> None:
    with pytest.raises(ExecutionProjectionError, match="deadline"):
        _fargate_runtime().templates.build(
            _PIPELINES,
            image=_FARGATE_IMAGE,
            project="unit-project",
            cpu=1,
            memory="2Gi",
            deadline_seconds=86_401,
            launcher_retry_count=1,
            batch_rows=1_000,
            require_guarded_free_tier=False,
            alert_target=None,
        )


def test_fargate_rejects_cron_semantics_eventbridge_cannot_preserve() -> None:
    pipelines = {
        "greenhouse_jobs": {
            **_PIPELINES["greenhouse_jobs"],
            "schedule": "0 9 1 * MON",
        }
    }
    with pytest.raises(ExecutionProjectionError, match="both day fields"):
        _fargate_runtime().templates.build(
            pipelines,
            image=_FARGATE_IMAGE,
            project="unit-project",
            cpu=1,
            memory="2Gi",
            deadline_seconds=900,
            launcher_retry_count=1,
            batch_rows=1_000,
            require_guarded_free_tier=False,
            alert_target=None,
        )


def test_fargate_rejects_provider_specific_cron_syntax() -> None:
    pipelines = {
        "greenhouse_jobs": {
            **_PIPELINES["greenhouse_jobs"],
            "schedule": "0 9 * * ?",
        }
    }
    with pytest.raises(ExecutionProjectionError, match="valid five-field"):
        _fargate_runtime().templates.build(
            pipelines,
            image=_FARGATE_IMAGE,
            project="unit-project",
            cpu=1,
            memory="2Gi",
            deadline_seconds=900,
            launcher_retry_count=1,
            batch_rows=1_000,
            require_guarded_free_tier=False,
            alert_target=None,
        )
