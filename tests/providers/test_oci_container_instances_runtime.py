"""OCI Container Instances launcher projection and fail-closed limits."""

from __future__ import annotations

import sys

import pytest

from dander.deployment import ExecutionProjectionError, LauncherRuntime, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry

_TENANCY_OCID = "ocid1.tenancy.oc1.." + "a" * 32
_COMPARTMENT_OCID = "ocid1.compartment.oc1.." + "b" * 32
_SUBNET_OCID = "ocid1.subnet.oc1.iad." + "c" * 32
_VAULT_OCID = "ocid1.vault.oc1.iad." + "d" * 32
_IMAGE = "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime@sha256:" + "e" * 64
_LAUNCHER = {
    "provider": "oci_container_instances",
    "region": "us-ashburn-1",
    "tenancy_id": _TENANCY_OCID,
    "compartment_id": _COMPARTMENT_OCID,
    "availability_domain": "Unit:US-ASHBURN-AD-1",
    "subnet_id": _SUBNET_OCID,
    "registry_namespace": "unitnamespace",
    "repository_name": "dander/runtime",
    "vault_id": _VAULT_OCID,
    "dynamic_group_name": "dander_phase7_runtime",
}
_PIPELINES: dict[str, dict[str, object]] = {
    "warehouse_fixture": {
        "runtime_service_account_id": "unused-on-oci",
        "build_models": True,
        "schedule": "15 4 * * *",
        "time_zone": "UTC",
        "paused": True,
        "secret_env": {"DANDER_POSTGRES_DSN": "postgres-dsn"},
    }
}


def _runtime(*, launcher: dict[str, object] | None = None) -> LauncherRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.LAUNCHER, launcher or _LAUNCHER)
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def _request(
    *,
    pipelines: dict[str, dict[str, object]] | None = None,
    image: str = _IMAGE,
    cpu: int = 1,
    memory: str = "2Gi",
) -> ResolvedTemplateRequest:
    return ResolvedTemplateRequest(
        pipelines=_PIPELINES if pipelines is None else pipelines,
        image=image,
        profile_id="oci_postgresql",
        cpu=cpu,
        memory=memory,
        deadline_seconds=900,
        launcher_retry_count=1,
        batch_rows=1_000,
        alert_target="oci_notifications",
    )


def test_oci_factory_is_lazy_and_projects_resource_principal_without_gcp_context() -> None:
    module_name = "dander.providers.oci_container_instances.runtime"
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

    assert runtime.provider_id == "oci_container_instances"
    assert runtime.region == "us-ashburn-1"
    assert template.image == _IMAGE
    assert template.profile_id == "oci_postgresql"
    assert template.resources.cpu_millis == 1_000
    assert template.resources.memory_mib == 2_048
    assert template.resources.ephemeral_storage_mib is None
    assert template.schedule.expression == "15 4 * * *"
    assert template.schedule.time_zone == "UTC"
    assert template.schedule.paused is True
    assert template.network.placement == _SUBNET_OCID
    assert dict(template.network.extensions) == {
        "oci_assign_public_ip": "false",
        "oci_availability_domain": "Unit:US-ASHBURN-AD-1",
    }
    assert template.workload_identity == (
        "oci-resource-principal://dynamic-group/dander_phase7_runtime"
    )
    assert environment["DANDER_OCI_REGION"] == "us-ashburn-1"
    assert environment["HOME"] == "/tmp"
    assert "DANDER_POSTGRES_DSN" not in environment
    assert secrets["DANDER_POSTGRES_DSN"].provider == "oci_vault"
    assert secrets["DANDER_POSTGRES_DSN"].reference == (
        f"oci-vault://{_VAULT_OCID}/secrets/postgres-dsn"
    )
    assert dict(template.extensions) == {
        "oci_compartment_id": _COMPARTMENT_OCID,
        "oci_graceful_shutdown_seconds": "120",
        "oci_registry_endpoint": "ocir.us-ashburn-1.oci.oraclecloud.com",
        "oci_restart_policy": "NEVER",
        "oci_shape": "CI.Standard.E4.Flex",
        "oci_tenancy_id": _TENANCY_OCID,
        "oci_vault_id": _VAULT_OCID,
    }
    serialized = repr(template.as_dict())
    assert "postgresql://" not in serialized
    assert "private_key" not in serialized


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"time_zone": "America/New_York"}, "require UTC"),
        ({"schedule": "0 9 * * ?"}, "five-field"),
        ({"secret_env": {"TOKEN": "not.a.vault.name"}}, "secret names"),
    ],
)
def test_oci_projection_rejects_unhonored_schedule_or_secret_intent(
    update: dict[str, object],
    message: str,
) -> None:
    pipeline = {**_PIPELINES["warehouse_fixture"], **update}
    with pytest.raises(ExecutionProjectionError, match=message):
        _runtime().templates.build(_request(pipelines={"warehouse_fixture": pipeline}))


@pytest.mark.parametrize(
    ("image", "cpu", "memory", "launcher_update", "message"),
    [
        (
            "ocir.us-phoenix-1.oci.oraclecloud.com/unitnamespace/dander/runtime@sha256:" + "e" * 64,
            1,
            "2Gi",
            {},
            "selected OCIR repository",
        ),
        (_IMAGE, 1, "1536Mi", {}, "whole-GiB"),
        (
            _IMAGE,
            4,
            "2Gi",
            {"shape": "CI.Standard.A1.Flex"},
            "CPU/memory pair",
        ),
    ],
)
def test_oci_projection_rejects_unowned_images_and_invalid_resource_pairs(
    image: str,
    cpu: int,
    memory: str,
    launcher_update: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ExecutionProjectionError, match=message):
        _runtime(launcher={**_LAUNCHER, **launcher_update}).templates.build(
            _request(image=image, cpu=cpu, memory=memory)
        )


@pytest.mark.parametrize(
    ("update", "field"),
    [
        ({"tenancy_id": "ocid1.tenancy.oc1.iad.invalid"}, "tenancy_id"),
        ({"repository_name": "dander//runtime"}, "repository_name"),
        ({"subnet_id": "ocid1.vcn.oc1.iad.invalid"}, "subnet_id"),
    ],
)
def test_oci_launcher_configuration_rejects_ambiguous_identifiers(
    update: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(ProviderFactoryError, match=field):
        default_provider_registry().parse(
            ProviderKind.LAUNCHER,
            {**_LAUNCHER, **update},
        )
