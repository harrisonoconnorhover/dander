"""Read-only Azure Container Apps deployment verification coverage."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from dander.providers.azure_container_apps import (
    AzureDeploymentBinding,
    AzureDeploymentVerificationError,
    AzureDeploymentVerifier,
)

_SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
_ROOT = f"/subscriptions/{_SUBSCRIPTION_ID}/resourceGroups/dander-phase6"
_ENVIRONMENT_ID = f"{_ROOT}/providers/Microsoft.App/managedEnvironments/dander-phase6-env"
_IDENTITY_ID = (
    f"{_ROOT}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
)
_IMAGE = "danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64


class _Runner:
    def __init__(self, payloads: dict[tuple[str, ...], object]) -> None:
        self.payloads = payloads
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == Path("/tmp/dander-azure-test")
        assert check and capture_output and text
        self.commands.append(args)
        suffix = args[1 : args.index("--subscription")]
        payload = self.payloads[suffix]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload))


def _binding() -> AzureDeploymentBinding:
    return AzureDeploymentBinding(
        subscription_id=_SUBSCRIPTION_ID,
        location="eastus",
        resource_group_name="dander-phase6",
        environment_name="dander-phase6-env",
        environment_id=_ENVIRONMENT_ID,
        acr_name="danderphase6",
        acr_login_server="danderphase6.azurecr.io",
        key_vault_name="dander-phase6-kv",
        key_vault_uri="https://dander-phase6-kv.vault.azure.net",
        managed_identity_id=_IDENTITY_ID,
        managed_identity_client_id=_CLIENT_ID,
        pipeline_id="warehouse_fixture",
        job_name="dander-00626d3b5f01",
        schedule_paused=True,
        runtime_timeout_seconds=900,
        runtime_max_retries=1,
        secret_provider="azure_key_vault",
        secret_bindings=(("DANDER_POSTGRES_DSN", "postgres-dsn"),),
        secret_ids=("postgres-dsn",),
        google_project=None,
        google_workload_identity_audience=None,
        google_application_id_uri=None,
        google_service_account=None,
        project_dir=Path("/tmp/dander-azure-test"),
    )


def _payloads() -> dict[tuple[str, ...], object]:
    binding = _binding()
    job_id = f"{_ROOT}/providers/Microsoft.App/jobs/{binding.job_name}"
    return {
        ("account", "show"): {"id": _SUBSCRIPTION_ID, "state": "Enabled"},
        (
            "containerapp",
            "env",
            "show",
            "--name",
            "dander-phase6-env",
            "--resource-group",
            "dander-phase6",
        ): {
            "id": _ENVIRONMENT_ID,
            "location": "East US",
            "properties": {
                "appLogsConfiguration": {
                    "logAnalyticsConfiguration": {"customerId": "workspace-customer-id"}
                }
            },
        },
        (
            "containerapp",
            "job",
            "show",
            "--name",
            binding.job_name,
            "--resource-group",
            "dander-phase6",
        ): {
            "id": job_id,
            "identity": {"userAssignedIdentities": {_IDENTITY_ID: {}}},
            "properties": {
                "environmentId": _ENVIRONMENT_ID,
                "configuration": {
                    "triggerType": "Manual",
                    "replicaTimeout": 900,
                    "replicaRetryLimit": 1,
                    "registries": [
                        {
                            "server": "danderphase6.azurecr.io",
                            "identity": _IDENTITY_ID,
                        }
                    ],
                    "secrets": [
                        {
                            "name": "secret-aabbccdd",
                            "identity": _IDENTITY_ID,
                            "keyVaultUrl": (
                                "https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn"
                            ),
                        }
                    ],
                },
                "template": {
                    "containers": [
                        {
                            "name": "runtime",
                            "image": _IMAGE,
                            "args": ["runtime", "execute"],
                            "env": [
                                {"name": "HOME", "value": "/tmp"},
                                {"name": "AZURE_CLIENT_ID", "value": _CLIENT_ID},
                                {
                                    "name": "DANDER_POSTGRES_DSN",
                                    "secretRef": "secret-aabbccdd",
                                },
                            ],
                        }
                    ]
                },
            },
        },
        (
            "acr",
            "show",
            "--name",
            "danderphase6",
            "--resource-group",
            "dander-phase6",
        ): {
            "id": f"{_ROOT}/providers/Microsoft.ContainerRegistry/registries/danderphase6",
            "loginServer": "danderphase6.azurecr.io",
            "adminUserEnabled": False,
        },
        (
            "keyvault",
            "show",
            "--name",
            "dander-phase6-kv",
            "--resource-group",
            "dander-phase6",
        ): {
            "id": f"{_ROOT}/providers/Microsoft.KeyVault/vaults/dander-phase6-kv",
            "properties": {
                "vaultUri": "https://dander-phase6-kv.vault.azure.net/",
                "enableRbacAuthorization": True,
                "networkAcls": {
                    "bypass": "None",
                    "defaultAction": "Deny",
                    "virtualNetworkRules": [
                        {
                            "id": (
                                f"{_ROOT}/providers/Microsoft.Network/virtualNetworks/"
                                "dander-phase6/subnets/container-apps"
                            )
                        }
                    ],
                },
            },
        },
    }


def _dict_payload(
    payloads: dict[tuple[str, ...], object],
    key: tuple[str, ...],
) -> dict[str, object]:
    payload = payloads[key]
    assert isinstance(payload, dict)
    return payload


def test_verifier_checks_exact_resources_without_reading_secret_values() -> None:
    runner = _Runner(_payloads())

    result = AzureDeploymentVerifier(_binding(), runner=runner).verify(expected_image=_IMAGE)

    assert result.image == _IMAGE
    assert result.trigger_type == "manual"
    assert result.managed_identity == _IDENTITY_ID
    assert result.log_analytics_workspace == "workspace-customer-id"
    flattened = " ".join(" ".join(command) for command in runner.commands)
    assert "secret show" not in flattened
    assert "secret list" not in flattened
    assert all("--only-show-errors" in command for command in runner.commands)


def test_verifier_checks_only_declared_secret_metadata_without_values() -> None:
    payloads = _payloads()
    payloads[
        (
            "keyvault",
            "secret",
            "list",
            "--vault-name",
            "dander-phase6-kv",
            "--maxresults",
            "25",
            "--query",
            "[].{id:id,enabled:attributes.enabled}",
        )
    ] = [
        {
            "id": "https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn",
            "enabled": True,
        },
        {
            "id": "https://dander-phase6-kv.vault.azure.net/secrets/unrelated-secret",
            "enabled": True,
        },
    ]
    runner = _Runner(payloads)

    result = AzureDeploymentVerifier(_binding(), runner=runner).verify_declared_secret_metadata()

    assert [metadata.as_dict() for metadata in result] == [
        {"name": "postgres-dsn", "enabled": True}
    ]
    flattened = " ".join(" ".join(command) for command in runner.commands)
    assert "secret list" in flattened
    assert "secret show" not in flattened
    assert "value" not in flattened


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        [
            {
                "id": "https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn",
                "enabled": False,
            }
        ],
    ],
)
def test_verifier_rejects_missing_or_disabled_declared_secret_metadata(
    metadata: list[dict[str, object]],
) -> None:
    payloads = _payloads()
    payloads[
        (
            "keyvault",
            "secret",
            "list",
            "--vault-name",
            "dander-phase6-kv",
            "--maxresults",
            "25",
            "--query",
            "[].{id:id,enabled:attributes.enabled}",
        )
    ] = metadata

    with pytest.raises(AzureDeploymentVerificationError, match="missing or disabled"):
        AzureDeploymentVerifier(
            _binding(), runner=_Runner(payloads)
        ).verify_declared_secret_metadata()


def test_verifier_checks_gcp_federation_and_runtime_secret_references() -> None:
    binding = replace(
        _binding(),
        secret_provider="gcp_secret_manager",
        secret_bindings=(("API_TOKEN", "source-api-token"),),
        secret_ids=("source-api-token",),
        google_project="unit-project",
        google_workload_identity_audience=(
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
        ),
        google_application_id_uri="api://77777777-7777-4777-8777-777777777777",
        google_service_account="dander-runtime@unit-project.iam.gserviceaccount.com",
    )
    payloads = _payloads()
    job_key = (
        "containerapp",
        "job",
        "show",
        "--name",
        binding.job_name,
        "--resource-group",
        "dander-phase6",
    )
    properties = _dict_payload(payloads, job_key)["properties"]
    assert isinstance(properties, dict)
    configuration = properties["configuration"]
    template = properties["template"]
    assert isinstance(configuration, dict)
    assert isinstance(template, dict)
    containers = template["containers"]
    assert isinstance(containers, list)
    container = containers[0]
    assert isinstance(container, dict)
    configuration["secrets"] = []
    container["env"] = [
        {"name": "HOME", "value": "/tmp"},
        {"name": "AZURE_CLIENT_ID", "value": _CLIENT_ID},
        {
            "name": "DANDER_AZURE_GCP_APPLICATION_ID_URI",
            "value": "api://77777777-7777-4777-8777-777777777777",
        },
        {
            "name": "DANDER_GCP_SERVICE_ACCOUNT",
            "value": "dander-runtime@unit-project.iam.gserviceaccount.com",
        },
        {
            "name": "DANDER_GCP_WIF_AUDIENCE",
            "value": (
                "//iam.googleapis.com/projects/1009770943166/locations/global/"
                "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
            ),
        },
        {"name": "GCP_PROJECT_ID", "value": "unit-project"},
        {
            "name": "API_TOKEN",
            "value": "projects/unit-project/secrets/source-api-token/versions/latest",
        },
    ]

    result = AzureDeploymentVerifier(binding, runner=_Runner(payloads)).verify(
        expected_image=_IMAGE
    )

    assert result.image == _IMAGE


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("image", "immutable runtime"),
        ("identity", "managed identity"),
        ("trigger", "trigger"),
        ("registry_admin", "artifact contract"),
        ("vault_rbac", "RBAC and network"),
        ("vault_network", "RBAC and network"),
    ],
)
def test_verifier_fails_closed_on_drift(mutation: str, message: str) -> None:
    payloads = _payloads()
    binding = _binding()
    job_key = (
        "containerapp",
        "job",
        "show",
        "--name",
        binding.job_name,
        "--resource-group",
        "dander-phase6",
    )
    job = _dict_payload(payloads, job_key)
    properties = job["properties"]
    assert isinstance(properties, dict)
    if mutation == "image":
        properties["template"]["containers"][0]["image"] = "mutable:latest"
    elif mutation == "identity":
        job["identity"] = {"userAssignedIdentities": {}}
    elif mutation == "trigger":
        properties["configuration"]["triggerType"] = "Schedule"
    elif mutation == "registry_admin":
        acr_key = (
            "acr",
            "show",
            "--name",
            "danderphase6",
            "--resource-group",
            "dander-phase6",
        )
        _dict_payload(payloads, acr_key)["adminUserEnabled"] = True
    elif mutation == "vault_rbac":
        vault_key = (
            "keyvault",
            "show",
            "--name",
            "dander-phase6-kv",
            "--resource-group",
            "dander-phase6",
        )
        _dict_payload(payloads, vault_key)["properties"]["enableRbacAuthorization"] = False  # type: ignore[index]
    else:
        vault_key = (
            "keyvault",
            "show",
            "--name",
            "dander-phase6-kv",
            "--resource-group",
            "dander-phase6",
        )
        _dict_payload(payloads, vault_key)["properties"]["networkAcls"]["virtualNetworkRules"] = []  # type: ignore[index]

    with pytest.raises(AzureDeploymentVerificationError, match=message):
        AzureDeploymentVerifier(binding, runner=_Runner(payloads)).verify(expected_image=_IMAGE)


def test_verifier_rejects_foreign_registry_before_provider_access() -> None:
    runner = _Runner(_payloads())
    with pytest.raises(AzureDeploymentVerificationError, match="deployment ACR"):
        AzureDeploymentVerifier(_binding(), runner=runner).verify(
            expected_image="otherazure.azurecr.io/dander@sha256:" + "a" * 64
        )
    assert runner.commands == []
