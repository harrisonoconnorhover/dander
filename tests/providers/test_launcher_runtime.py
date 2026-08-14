"""Launcher-provider selection and projection parity coverage."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError

import pytest

from dander.deployment import (
    ExecutionProjectionError,
    LauncherRuntime,
    ResolvedTemplateRequest,
    build_gcp_execution_templates,
)
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.gcp_launcher import GcpLauncherContext, gcp_launcher_factory_context

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


def _request(
    *,
    pipelines: dict[str, dict[str, object]] | None = None,
    image: str = _IMAGE,
    profile_id: str = "gcp",
    cpu: int = 1,
    memory: str = "512Mi",
    deadline_seconds: int = 300,
    launcher_retry_count: int = 1,
    batch_rows: int = 10_000,
    alert_target: str | None = None,
) -> ResolvedTemplateRequest:
    return ResolvedTemplateRequest(
        pipelines=_PIPELINES if pipelines is None else pipelines,
        image=image,
        profile_id=profile_id,
        cpu=cpu,
        memory=memory,
        deadline_seconds=deadline_seconds,
        launcher_retry_count=launcher_retry_count,
        batch_rows=batch_rows,
        alert_target=alert_target,
    )


def _gcp_context(*, guarded: bool = False) -> dict[str, object]:
    return gcp_launcher_factory_context(
        GcpLauncherContext(
            project="unit-project",
            require_guarded_free_tier=guarded,
        )
    )


def _templates(runtime: LauncherRuntime) -> dict[str, object]:
    return {
        pipeline_id: template.as_dict()
        for pipeline_id, template in runtime.templates.build(
            _request(alert_target="operator@example.invalid")
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
    runtime = registry.build(ProviderKind.LAUNCHER, config, context=_gcp_context())

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


def _fargate_runtime(*, guarded: bool = False) -> LauncherRuntime:
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
    runtime = registry.build(
        ProviderKind.LAUNCHER,
        config,
        context=_gcp_context(guarded=guarded),
    )
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
    runtime = registry.build(ProviderKind.LAUNCHER, config, context=_gcp_context())
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
        _request(
            pipelines=pipelines,
            image=_FARGATE_IMAGE,
            memory="2Gi",
            deadline_seconds=900,
            batch_rows=1_000,
            alert_target="arn:aws:sns:us-east-1:184463061564:dander-failures",
        )
    )["salesforce_crm"]

    assert template.launcher == "fargate"
    assert template.workload_identity == (
        "arn:aws:iam::184463061564:role/dander-runtime-salesforce"
    )
    assert template.resources.ephemeral_storage_mib == 20_480
    assert dict(template.environment)["DANDER_GCP_SERVICE_ACCOUNT"] == (
        "dander-runtime-salesforce@unit-project.iam.gserviceaccount.com"
    )
    assert dict(template.environment)["HOME"] == "/tmp"
    assert dict(template.environment)["TMPDIR"] == "/tmp"
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


def _aws_native_fargate_runtime() -> LauncherRuntime:
    from dander.providers.aws_secrets_manager import AwsSecretsManagerConfig
    from dander.providers.fargate.runtime import (
        FargateProfileContext,
        fargate_profile_factory_context,
    )
    from dander.providers.glue import GlueCatalogConfig
    from dander.providers.postgresql import PostgreSQLStateConfig
    from dander.providers.redshift import RedshiftWarehouseConfig

    registry = default_provider_registry()
    launcher = registry.parse(
        ProviderKind.LAUNCHER,
        {
            "provider": "fargate",
            "region": "us-east-1",
            "aws_account_id": "184463061564",
            "subnet_ids": ["subnet-0123456789abcdef0"],
            "security_group_ids": ["sg-0123456789abcdef0"],
        },
    )
    context = FargateProfileContext(
        profile_id="aws_native",
        warehouse=RedshiftWarehouseConfig(
            provider="redshift",
            deployment="provisioned",
            host="dander.abc123.us-east-1.redshift.amazonaws.com",
            database="analytics",
            schema_name="raw",
            db_user="dander_runtime",
            region="us-east-1",
            cluster_identifier="dander-phase8",
            copy_role_arn="arn:aws:iam::184463061564:role/DanderRedshiftCopy",
            staging_bucket="dander-phase8-staging",
        ),
        state=PostgreSQLStateConfig(
            provider="postgresql",
            authority_id="postgresql:aws-native",
        ),
        catalog=GlueCatalogConfig(
            provider="glue",
            region="us-east-1",
            catalog_id="184463061564",
        ),
        secrets=AwsSecretsManagerConfig(
            provider="aws_secret_manager",
            region="us-east-1",
        ),
    )
    runtime = registry.build(
        ProviderKind.LAUNCHER,
        launcher,
        context=fargate_profile_factory_context(context),
    )
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def test_fargate_projects_the_typed_aws_native_profile_keylessly() -> None:
    secret = (
        "aws-sm://arn:aws:secretsmanager:us-east-1:184463061564:secret:dander/postgres-dsn-AbCdEf"
    )
    pipelines = {
        "greenhouse_jobs": {
            **_PIPELINES["greenhouse_jobs"],
            "secret_env": {"DANDER_POSTGRES_DSN": secret},
        }
    }

    template = _aws_native_fargate_runtime().templates.build(
        _request(
            pipelines=pipelines,
            image=_FARGATE_IMAGE,
            profile_id="aws_native",
            memory="2Gi",
        )
    )["greenhouse_jobs"]

    assert template.profile_id == "aws_native"
    assert template.command[template.command.index("--platform") + 1] == "aws_native"
    binding = dict(template.secret_bindings)["DANDER_POSTGRES_DSN"]
    assert binding.provider == "aws_secret_manager"
    assert binding.reference == secret
    environment = dict(template.environment)
    assert environment["AWS_REGION"] == "us-east-1"
    assert "GCP_PROJECT_ID" not in environment
    assert "DANDER_GCP_WIF_AUDIENCE" not in environment
    assert "AWS_ACCESS_KEY_ID" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_fargate_aws_native_rejects_a_secret_from_another_account() -> None:
    pipelines = {
        "greenhouse_jobs": {
            **_PIPELINES["greenhouse_jobs"],
            "secret_env": {
                "DANDER_POSTGRES_DSN": (
                    "aws-sm://arn:aws:secretsmanager:us-east-1:999999999999:"
                    "secret:dander/postgres-dsn-AbCdEf"
                )
            },
        }
    }

    with pytest.raises(ExecutionProjectionError, match="launcher account and region"):
        _aws_native_fargate_runtime().templates.build(
            _request(
                pipelines=pipelines,
                image=_FARGATE_IMAGE,
                profile_id="aws_native",
                memory="2Gi",
            )
        )


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
        _fargate_runtime(guarded=guarded).templates.build(
            _request(
                image=_FARGATE_IMAGE,
                memory=memory,
                deadline_seconds=900,
                batch_rows=1_000,
            )
        )


def test_fargate_accepts_a_run_longer_than_one_task_role_session() -> None:
    template = _fargate_runtime().templates.build(
        _request(
            image=_FARGATE_IMAGE,
            memory="2Gi",
            deadline_seconds=3_601,
            batch_rows=1_000,
        )
    )["greenhouse_jobs"]

    assert template.resources.deadline_seconds == 3_601


def test_fargate_rejects_a_run_longer_than_one_day() -> None:
    with pytest.raises(ExecutionProjectionError, match="deadline"):
        _fargate_runtime().templates.build(
            _request(
                image=_FARGATE_IMAGE,
                memory="2Gi",
                deadline_seconds=86_401,
                batch_rows=1_000,
            )
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
            _request(
                pipelines=pipelines,
                image=_FARGATE_IMAGE,
                memory="2Gi",
                deadline_seconds=900,
                batch_rows=1_000,
            )
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
            _request(
                pipelines=pipelines,
                image=_FARGATE_IMAGE,
                memory="2Gi",
                deadline_seconds=900,
                batch_rows=1_000,
            )
        )


def test_resolved_template_request_is_provider_neutral_and_immutable() -> None:
    request = _request()

    assert "project" not in request.__dataclass_fields__
    assert "require_guarded_free_tier" not in request.__dataclass_fields__
    with pytest.raises(TypeError):
        request.pipelines["other"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        request.pipelines["greenhouse_jobs"]["paused"] = False  # type: ignore[index]
    secret_env = request.pipelines["greenhouse_jobs"]["secret_env"]
    assert isinstance(secret_env, dict) is False
    with pytest.raises(TypeError):
        secret_env["TOKEN"] = "secret-id"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.profile_id = "postgres"  # type: ignore[misc]
