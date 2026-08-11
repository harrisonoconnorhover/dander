"""Azure Container Apps Jobs launcher projection and limit coverage."""

from __future__ import annotations

import sys

import pytest

from dander.deployment import ExecutionProjectionError, LauncherRuntime, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry

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


def _runtime() -> LauncherRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.LAUNCHER, _LAUNCHER)
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def _request(
    *,
    pipelines: dict[str, dict[str, object]] | None = None,
    image: str = _IMAGE,
    memory: str = "2Gi",
) -> ResolvedTemplateRequest:
    return ResolvedTemplateRequest(
        pipelines=_PIPELINES if pipelines is None else pipelines,
        image=image,
        profile_id="azure_snowflake",
        cpu=1,
        memory=memory,
        deadline_seconds=900,
        launcher_retry_count=1,
        batch_rows=1_000,
        alert_target=(
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "resourceGroups/dander-phase6/providers/microsoft.insights/"
            "actionGroups/dander-phase6"
        ),
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
