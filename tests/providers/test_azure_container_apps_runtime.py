"""Azure Container Apps Jobs launcher projection and limit coverage."""

from __future__ import annotations

import sys

import pytest

from dander.deployment import ExecutionProjectionError, LauncherRuntime, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.gcp_launcher import GcpLauncherContext, gcp_launcher_factory_context

_SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
_IMAGE = "danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64
_LAUNCHER = {
    "provider": "azure_container_apps",
    "region": "eastus",
    "subscription_id": _SUBSCRIPTION_ID,
    "resource_group_name": "dander-phase6",
    "container_app_environment_name": "dander-phase6-env",
    "acr_name": "danderphase6",
    "key_vault_name": "dander-phase6-kv",
    "managed_identity_name": "dander-phase6-runtime",
    "managed_identity_client_id": _CLIENT_ID,
}
_PIPELINES: dict[str, dict[str, object]] = {
    "warehouse_fixture": {
        "runtime_service_account_id": "dander-runtime",
        "build_models": True,
        "schedule": "15 4 * * *",
        "time_zone": "UTC",
        "paused": True,
        "secret_env": {
            "DANDER_POSTGRES_DSN": "postgres-dsn",
            "DANDER_SNOWFLAKE_OAUTH_TOKEN": "snowflake-oauth-token",
        },
    }
}


def _runtime(
    *,
    launcher: dict[str, object] | None = None,
    gcp: GcpLauncherContext | None = None,
) -> LauncherRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.LAUNCHER, launcher or _LAUNCHER)
    runtime = registry.build(
        ProviderKind.LAUNCHER,
        config,
        context=gcp_launcher_factory_context(gcp) if gcp is not None else None,
    )
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def _request(
    *,
    pipelines: dict[str, dict[str, object]] | None = None,
    image: str = _IMAGE,
    memory: str = "2Gi",
    profile_id: str = "azure_snowflake",
    deployment_id: str | None = None,
    platforms_config_json: str | None = None,
) -> ResolvedTemplateRequest:
    return ResolvedTemplateRequest(
        pipelines=_PIPELINES if pipelines is None else pipelines,
        image=image,
        profile_id=profile_id,
        cpu=1,
        memory=memory,
        deadline_seconds=900,
        launcher_retry_count=1,
        batch_rows=1_000,
        deployment_id=deployment_id,
        alert_target=(
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "resourceGroups/dander-phase6/providers/microsoft.insights/"
            "actionGroups/dander-phase6"
        ),
        platforms_config_json=platforms_config_json,
    )


def test_azure_factory_is_lazy_and_projects_only_non_secret_identity() -> None:
    module_name = "dander.providers.azure_container_apps.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.LAUNCHER, _LAUNCHER)

    assert module_name not in sys.modules
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    assert module_name in sys.modules

    template = runtime.templates.build(_request())["warehouse_fixture"]
    environment = dict(template.environment)
    secrets = dict(template.secret_bindings)

    assert runtime.provider_id == "azure_container_apps"
    assert runtime.region == "eastus"
    assert template.image == _IMAGE
    assert template.profile_id == "azure_snowflake"
    assert template.resources.cpu_millis == 1_000
    assert template.resources.memory_mib == 2_048
    assert template.resources.ephemeral_storage_mib is None
    assert template.schedule.expression == "15 4 * * *"
    assert template.schedule.time_zone == "UTC"
    assert template.schedule.paused is True
    assert template.network.placement is not None
    assert template.network.placement.endswith(
        "/providers/Microsoft.App/managedEnvironments/dander-phase6-env"
    )
    assert template.workload_identity.endswith(
        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
    )
    assert environment["AZURE_CLIENT_ID"] == _CLIENT_ID
    assert environment["HOME"] == "/tmp"
    assert "DANDER_POSTGRES_DSN" not in environment
    assert secrets["DANDER_POSTGRES_DSN"].reference == (
        "azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn"
    )
    assert secrets["DANDER_SNOWFLAKE_OAUTH_TOKEN"].provider == "azure_key_vault"
    assert dict(template.extensions) == {
        "azure_acr_login_server": "danderphase6.azurecr.io",
        "azure_key_vault_uri": "https://dander-phase6-kv.vault.azure.net",
        "azure_managed_identity_client_id": _CLIENT_ID,
    }
    serialized = repr(template.as_dict())
    assert "oauth-token-value" not in serialized
    assert "postgresql://" not in serialized


def test_azure_factory_projects_resolved_platform_overlay() -> None:
    template = _runtime().templates.build(
        _request(
            deployment_id="phase8_azure",
            platforms_config_json='{"version":1}',
        )
    )["warehouse_fixture"]

    assert dict(template.environment)["DANDER_PLATFORMS_CONFIG_JSON"] == '{"version":1}'
    assert template.profile_id == "phase8_azure"
    platform_index = template.command.index("--platform") + 1
    assert template.command[platform_index] == "phase8_azure"


def test_azure_factory_projects_keyless_google_federation_only_for_gcp_profile() -> None:
    launcher: dict[str, object] = {
        **_LAUNCHER,
        "google_workload_identity_audience": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
        ),
        "google_application_id_uri": "api://33333333-3333-4333-8333-333333333333",
    }
    runtime = _runtime(
        launcher=launcher,
        gcp=GcpLauncherContext(project="unit-project", require_guarded_free_tier=False),
    )

    template = runtime.templates.build(
        _request(
            pipelines={
                "warehouse_fixture": {
                    **_PIPELINES["warehouse_fixture"],
                    "runtime_service_account_id": "dander-runtime",
                    "secret_env": {"API_TOKEN": "source-api-token"},
                }
            },
            profile_id="gcp",
        )
    )["warehouse_fixture"]
    environment = dict(template.environment)

    assert environment["DANDER_AZURE_GCP_APPLICATION_ID_URI"] == (
        "api://33333333-3333-4333-8333-333333333333"
    )
    assert environment["DANDER_GCP_SERVICE_ACCOUNT"] == (
        "dander-runtime@unit-project.iam.gserviceaccount.com"
    )
    assert environment["GCP_PROJECT_ID"] == "unit-project"
    assert dict(template.secret_bindings)["API_TOKEN"].reference == (
        "gcp-sm://projects/unit-project/secrets/source-api-token/versions/latest"
    )


def test_azure_google_federation_rejects_missing_gcp_context() -> None:
    launcher: dict[str, object] = {
        **_LAUNCHER,
        "google_workload_identity_audience": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
        ),
        "google_application_id_uri": "api://33333333-3333-4333-8333-333333333333",
    }

    with pytest.raises(ExecutionProjectionError, match="GCP platform"):
        _runtime(launcher=launcher).templates.build(_request())


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"time_zone": "America/New_York"}, "require UTC"),
        ({"schedule": "0 9 * * ?"}, "five-field"),
        ({"secret_env": {"TOKEN": "not_a_key_vault_name"}}, "secret ids"),
    ],
)
def test_azure_projection_rejects_unhonored_schedule_or_secret_intent(
    update: dict[str, object],
    message: str,
) -> None:
    pipeline = {**_PIPELINES["warehouse_fixture"], **update}
    with pytest.raises(ExecutionProjectionError, match=message):
        _runtime().templates.build(_request(pipelines={"warehouse_fixture": pipeline}))


@pytest.mark.parametrize(
    ("image", "memory", "message"),
    [
        (
            "other.azurecr.io/dander/runtime@sha256:" + "a" * 64,
            "2Gi",
            "selected ACR",
        ),
        (_IMAGE, "512Mi", "CPU/memory"),
    ],
)
def test_azure_projection_rejects_unowned_images_and_invalid_resource_pairs(
    image: str,
    memory: str,
    message: str,
) -> None:
    with pytest.raises(ExecutionProjectionError, match=message):
        _runtime().templates.build(_request(image=image, memory=memory))


def test_azure_launcher_configuration_rejects_ambiguous_names() -> None:
    registry = default_provider_registry()
    with pytest.raises(ProviderFactoryError, match="key_vault_name"):
        registry.parse(
            ProviderKind.LAUNCHER,
            {**_LAUNCHER, "key_vault_name": "dander--phase6"},
        )
