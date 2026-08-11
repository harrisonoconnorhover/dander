"""Read-only Azure Container Apps deployment verification through the Azure CLI."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from pathlib import Path

_DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_PIPELINE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ACR_IMAGE = re.compile(
    r"^(?P<registry>[a-z][a-z0-9]{4,49})\.azurecr\.io/"
    r"[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$"
)


class AzureDeploymentVerificationError(RuntimeError):
    """Raised when read-only Azure deployment checks fail or return invalid data."""


class _Runner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    args: tuple[str, ...],
    *,
    cwd: Path,
    check: bool,
    capture_output: bool,
    text: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
    )


@dataclass(frozen=True, slots=True)
class AzureDeploymentBinding:
    """Exact manifest pipeline to Azure resource binding."""

    subscription_id: str
    location: str
    resource_group_name: str
    environment_name: str
    environment_id: str
    acr_name: str
    acr_login_server: str
    key_vault_name: str
    key_vault_uri: str
    managed_identity_id: str
    managed_identity_client_id: str
    pipeline_id: str
    job_name: str
    schedule_paused: bool
    runtime_timeout_seconds: int
    runtime_max_retries: int
    secret_provider: str
    secret_bindings: tuple[tuple[str, str], ...]
    secret_ids: tuple[str, ...]
    google_project: str | None
    google_workload_identity_audience: str | None
    google_application_id_uri: str | None
    google_service_account: str | None
    project_dir: Path

    @classmethod
    def from_project(
        cls,
        *,
        config: Path,
        deployment: str,
        pipeline_id: str,
        name: str = "dander",
        gcp_project: str | None = None,
    ) -> AzureDeploymentBinding:
        """Resolve one Azure pipeline from the validated project manifest."""
        resolved_config = config.expanduser().resolve()
        if _DEPLOYMENT_NAME.fullmatch(name) is None:
            raise AzureDeploymentVerificationError("Invalid Azure deployment name")
        if _PIPELINE_ID.fullmatch(pipeline_id) is None:
            raise AzureDeploymentVerificationError("Invalid pipeline identifier")
        try:
            manifest = load_project_config(resolved_config, deployment=deployment)
            if manifest.launcher_provider != "azure_container_apps":
                raise ProjectConfigError(
                    f"Deployment {deployment!r} does not select "
                    "launcher.provider='azure_container_apps'"
                )
            pipeline = manifest.pipelines[pipeline_id]
            manifest.validate_references(resolved_config.parent)
            launcher = manifest.resolved_launcher_config()
        except KeyError as error:
            raise AzureDeploymentVerificationError(
                f"Pipeline {pipeline_id!r} is not declared in the project manifest"
            ) from error
        except ProjectConfigError as error:
            raise AzureDeploymentVerificationError(str(error)) from error
        subscription_id = _uuid_string(launcher.get("subscription_id"), "subscription id")
        client_id = _uuid_string(launcher.get("managed_identity_client_id"), "identity client id")
        required = {
            key: value
            for key in (
                "region",
                "resource_group_name",
                "container_app_environment_name",
                "acr_name",
                "key_vault_name",
                "managed_identity_name",
            )
            if isinstance(value := launcher.get(key), str) and value
        }
        if len(required) != 6:
            raise AzureDeploymentVerificationError("Azure launcher configuration is incomplete")
        resource_group = required["resource_group_name"]
        environment_name = required["container_app_environment_name"]
        identity_name = required["managed_identity_name"]
        resource_root = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        google_project = None
        google_service_account = None
        google_audience = None
        google_application_id_uri = None
        if manifest.secret_provider == "gcp_secret_manager":
            configured_project = manifest.warehouse_config.get("project")
            if gcp_project is not None and _GCP_PROJECT.fullmatch(gcp_project) is None:
                raise AzureDeploymentVerificationError("Invalid GCP project identifier")
            if (
                isinstance(configured_project, str)
                and gcp_project is not None
                and configured_project != gcp_project
            ):
                raise AzureDeploymentVerificationError(
                    "Explicit GCP project does not match the project manifest"
                )
            project_value = gcp_project or configured_project
            runtime_id = manifest.terraform_pipelines()[pipeline_id].get(
                "runtime_service_account_id"
            )
            google_audience = launcher.get("google_workload_identity_audience")
            google_application_id_uri = launcher.get("google_application_id_uri")
            if not all(
                isinstance(value, str) and value
                for value in (
                    project_value,
                    runtime_id,
                    google_audience,
                    google_application_id_uri,
                )
            ):
                raise AzureDeploymentVerificationError(
                    "Azure BigQuery federation configuration is incomplete"
                )
            google_project = cast("str", project_value)
            google_service_account = f"{runtime_id}@{google_project}.iam.gserviceaccount.com"
        return cls(
            subscription_id=subscription_id,
            location=required["region"],
            resource_group_name=resource_group,
            environment_name=environment_name,
            environment_id=(
                f"{resource_root}/providers/Microsoft.App/managedEnvironments/{environment_name}"
            ),
            acr_name=required["acr_name"],
            acr_login_server=f"{required['acr_name']}.azurecr.io",
            key_vault_name=required["key_vault_name"],
            key_vault_uri=f"https://{required['key_vault_name']}.vault.azure.net",
            managed_identity_id=(
                f"{resource_root}/providers/Microsoft.ManagedIdentity/"
                f"userAssignedIdentities/{identity_name}"
            ),
            managed_identity_client_id=client_id,
            pipeline_id=pipeline_id,
            job_name=_job_name(name=name, pipeline_id=pipeline_id),
            schedule_paused=pipeline.paused,
            runtime_timeout_seconds=manifest.platform.runtime.timeout_seconds,
            runtime_max_retries=manifest.platform.runtime.max_retries,
            secret_provider=manifest.secret_provider,
            secret_bindings=tuple(sorted(pipeline.secrets.items())),
            secret_ids=tuple(sorted(pipeline.secrets.values())),
            google_project=google_project,
            google_workload_identity_audience=(
                cast("str", google_audience) if google_audience is not None else None
            ),
            google_application_id_uri=(
                cast("str", google_application_id_uri)
                if google_application_id_uri is not None
                else None
            ),
            google_service_account=google_service_account,
            project_dir=resolved_config.parent,
        )


@dataclass(frozen=True, slots=True)
class AzureDeploymentVerification:
    """Sanitized read-only verification result for one Azure pipeline."""

    subscription: str
    resource_group: str
    environment: str
    job: str
    trigger_type: str
    image: str
    registry: str
    managed_identity: str
    key_vault: str
    log_analytics_workspace: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AzureSecretMetadata:
    """Sanitized metadata for one manifest-declared Key Vault secret."""

    name: str
    enabled: bool

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


class AzureDeploymentVerifier:
    """Verify one manifest-bound Container Apps Job without reading secret values."""

    def __init__(
        self,
        binding: AzureDeploymentBinding,
        *,
        runner: _Runner | None = None,
    ) -> None:
        self.binding = binding
        self._runner = runner or _subprocess_runner

    def verify(self, *, expected_image: str) -> AzureDeploymentVerification:
        """Verify identity, immutable image, state, logs, registry, and Key Vault."""
        image = _ACR_IMAGE.fullmatch(expected_image)
        if image is None or image.group("registry") != self.binding.acr_name:
            raise AzureDeploymentVerificationError(
                "Expected image is not an immutable image in this deployment ACR"
            )
        account = self._json("account", "show")
        if (
            str(account.get("id", "")).lower() != self.binding.subscription_id.lower()
            or account.get("state") != "Enabled"
        ):
            raise AzureDeploymentVerificationError(
                "Authenticated Azure subscription is not enabled or does not match"
            )
        environment = self._json(
            "containerapp",
            "env",
            "show",
            "--name",
            self.binding.environment_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        if (
            str(environment.get("id", "")).lower() != self.binding.environment_id.lower()
            or str(environment.get("location", "")).replace(" ", "").lower()
            != self.binding.location.lower()
        ):
            raise AzureDeploymentVerificationError(
                "Container Apps environment does not match the manifest"
            )
        workspace_id = _nested_string(
            environment,
            "properties",
            "appLogsConfiguration",
            "logAnalyticsConfiguration",
            "customerId",
        )
        if workspace_id is None:
            workspace_id = _nested_string(
                environment,
                "appLogsConfiguration",
                "logAnalyticsConfiguration",
                "customerId",
            )
        if workspace_id is None:
            raise AzureDeploymentVerificationError(
                "Container Apps environment has no Log Analytics destination"
            )
        job = self._json(
            "containerapp",
            "job",
            "show",
            "--name",
            self.binding.job_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        properties = _mapping(job.get("properties")) or job
        configuration = _mapping(properties.get("configuration"))
        template = _mapping(properties.get("template"))
        identity = _mapping(job.get("identity"))
        expected_trigger = "Manual" if self.binding.schedule_paused else "Schedule"
        if (
            str(job.get("id", "")).rsplit("/", maxsplit=1)[-1] != self.binding.job_name
            or properties.get("environmentId") != self.binding.environment_id
            or configuration.get("triggerType") != expected_trigger
            or configuration.get("replicaTimeout") != self.binding.runtime_timeout_seconds
            or configuration.get("replicaRetryLimit") != self.binding.runtime_max_retries
        ):
            raise AzureDeploymentVerificationError(
                "Container Apps Job does not match manifest trigger, timeout, or retry intent"
            )
        identities = _mapping(identity.get("userAssignedIdentities"))
        if not any(key.lower() == self.binding.managed_identity_id.lower() for key in identities):
            raise AzureDeploymentVerificationError(
                "Container Apps Job is missing the selected managed identity"
            )
        registries = configuration.get("registries")
        if not isinstance(registries, list) or not any(
            isinstance(item, dict)
            and item.get("server") == self.binding.acr_login_server
            and str(item.get("identity", "")).lower() == self.binding.managed_identity_id.lower()
            for item in registries
        ):
            raise AzureDeploymentVerificationError(
                "Container Apps Job registry identity does not match the manifest"
            )
        secrets = configuration.get("secrets")
        if secrets is None:
            secrets = []
        if not isinstance(secrets, list):
            raise AzureDeploymentVerificationError(
                "Container Apps Job returned invalid Key Vault references"
            )
        expected_secret_urls = (
            {
                f"{self.binding.key_vault_uri}/secrets/{secret_id}"
                for secret_id in self.binding.secret_ids
            }
            if self.binding.secret_provider == "azure_key_vault"
            else set()
        )
        actual_secret_urls = {
            str(item.get("keyVaultUrl") or item.get("keyVaultSecretId"))
            for item in secrets
            if isinstance(item, dict)
            and str(item.get("identity", "")).lower() == self.binding.managed_identity_id.lower()
        }
        if actual_secret_urls != expected_secret_urls:
            raise AzureDeploymentVerificationError(
                "Container Apps Job Key Vault references do not match the manifest"
            )
        containers = template.get("containers")
        if (
            not isinstance(containers, list)
            or len(containers) != 1
            or not isinstance(containers[0], dict)
            or containers[0].get("image") != expected_image
            or containers[0].get("args", [None])[0] != "runtime"
        ):
            raise AzureDeploymentVerificationError(
                "Container Apps Job does not use the expected immutable runtime"
            )
        environment_values = {
            item.get("name"): item.get("value")
            for item in containers[0].get("env", [])
            if isinstance(item, dict) and "value" in item
        }
        if (
            environment_values.get("HOME") != "/tmp"
            or environment_values.get("AZURE_CLIENT_ID") != self.binding.managed_identity_client_id
        ):
            raise AzureDeploymentVerificationError(
                "Container Apps Job environment does not match the runtime contract"
            )
        if self.binding.secret_provider == "gcp_secret_manager":
            expected_google_environment = {
                "DANDER_AZURE_GCP_APPLICATION_ID_URI": self.binding.google_application_id_uri,
                "DANDER_GCP_SERVICE_ACCOUNT": self.binding.google_service_account,
                "DANDER_GCP_WIF_AUDIENCE": self.binding.google_workload_identity_audience,
                "GCP_PROJECT_ID": self.binding.google_project,
                **{
                    environment_name: (
                        f"projects/{self.binding.google_project}/secrets/"
                        f"{secret_id}/versions/latest"
                    )
                    for environment_name, secret_id in self.binding.secret_bindings
                },
            }
            if any(
                environment_values.get(name) != value
                for name, value in expected_google_environment.items()
            ):
                raise AzureDeploymentVerificationError(
                    "Container Apps Job Google federation environment does not match the manifest"
                )
        registry = self._json(
            "acr",
            "show",
            "--name",
            self.binding.acr_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        if (
            registry.get("loginServer") != self.binding.acr_login_server
            or registry.get("adminUserEnabled") is not False
        ):
            raise AzureDeploymentVerificationError(
                "ACR does not meet the runtime artifact contract"
            )
        vault = self._json(
            "keyvault",
            "show",
            "--name",
            self.binding.key_vault_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        vault_properties = _mapping(vault.get("properties"))
        vault_network_acls = _mapping(vault_properties.get("networkAcls"))
        vault_subnet_rules = vault_network_acls.get("virtualNetworkRules")
        if (
            str(vault_properties.get("vaultUri", "")).rstrip("/") != self.binding.key_vault_uri
            or vault_properties.get("enableRbacAuthorization") is not True
            or vault_network_acls.get("defaultAction") != "Deny"
            or vault_network_acls.get("bypass") != "None"
            or (
                self.binding.secret_provider == "azure_key_vault"
                and (
                    not isinstance(vault_subnet_rules, list)
                    or not any(
                        isinstance(rule, dict) and isinstance(rule.get("id"), str)
                        for rule in vault_subnet_rules
                    )
                )
            )
        ):
            raise AzureDeploymentVerificationError(
                "Key Vault does not meet the RBAC and network secret-reference contract"
            )
        return AzureDeploymentVerification(
            subscription=self.binding.subscription_id,
            resource_group=self.binding.resource_group_name,
            environment=self.binding.environment_id,
            job=cast("str", job["id"]),
            trigger_type=expected_trigger.lower(),
            image=expected_image,
            registry=cast("str", registry["id"]),
            managed_identity=self.binding.managed_identity_id,
            key_vault=cast("str", vault["id"]),
            log_analytics_workspace=workspace_id,
        )

    def verify_declared_secret_metadata(self) -> tuple[AzureSecretMetadata, ...]:
        """Verify declared Key Vault secret names and enabled state without reading values."""
        if self.binding.secret_provider != "azure_key_vault":
            raise AzureDeploymentVerificationError(
                "Secret metadata verification requires Azure Key Vault"
            )
        payload = self._json_list(
            "keyvault",
            "secret",
            "list",
            "--vault-name",
            self.binding.key_vault_name,
            "--maxresults",
            "25",
            "--query",
            "[].{id:id,enabled:attributes.enabled}",
        )
        prefix = f"{self.binding.key_vault_uri}/secrets/"
        available: dict[str, bool] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise AzureDeploymentVerificationError(
                    "Azure CLI returned invalid Key Vault secret metadata"
                )
            identifier = item.get("id")
            enabled = item.get("enabled")
            if (
                not isinstance(identifier, str)
                or not identifier.startswith(prefix)
                or "/" in (secret_name := identifier.removeprefix(prefix))
                or not secret_name
                or not isinstance(enabled, bool)
                or secret_name in available
            ):
                raise AzureDeploymentVerificationError(
                    "Azure CLI returned invalid Key Vault secret metadata"
                )
            available[secret_name] = enabled
        declared = set(self.binding.secret_ids)
        missing = declared.difference(available)
        disabled = {secret_name for secret_name in declared if available.get(secret_name) is False}
        if missing or disabled:
            raise AzureDeploymentVerificationError(
                "Declared Key Vault secrets are missing or disabled"
            )
        return tuple(
            AzureSecretMetadata(name=secret_name, enabled=True) for secret_name in sorted(declared)
        )

    def _json(self, *args: str) -> dict[str, object]:
        payload = self._json_payload(*args)
        if not isinstance(payload, dict):
            raise AzureDeploymentVerificationError(
                "Azure CLI returned an invalid verification response"
            )
        return payload

    def _json_list(self, *args: str) -> list[object]:
        payload = self._json_payload(*args)
        if not isinstance(payload, list):
            raise AzureDeploymentVerificationError(
                "Azure CLI returned an invalid verification response"
            )
        return payload

    def _json_payload(self, *args: str) -> object:
        command = (
            "az",
            *args,
            "--subscription",
            self.binding.subscription_id,
            "--output",
            "json",
            "--only-show-errors",
        )
        try:
            result = self._runner(
                command,
                cwd=self.binding.project_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as error:
            raise AzureDeploymentVerificationError(
                "Azure CLI is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            raise AzureDeploymentVerificationError(
                f"Azure read-only verification failed with exit code {error.returncode}"
            ) from error
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AzureDeploymentVerificationError("Azure CLI returned invalid JSON") from error
        return payload


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _nested_string(document: dict[str, object], *path: str) -> str | None:
    value: object = document
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) and value else None


def _uuid_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AzureDeploymentVerificationError(f"Azure launcher has invalid {label}")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise AzureDeploymentVerificationError(f"Azure launcher has invalid {label}") from error
    if parsed.variant != "specified in RFC 4122":
        raise AzureDeploymentVerificationError(f"Azure launcher has invalid {label}")
    return value


def _job_name(*, name: str, pipeline_id: str) -> str:
    return f"{name[:12]}-{hashlib.sha1(pipeline_id.encode()).hexdigest()[:12]}"  # noqa: S324


__all__ = [
    "AzureDeploymentBinding",
    "AzureDeploymentVerification",
    "AzureDeploymentVerificationError",
    "AzureDeploymentVerifier",
    "AzureSecretMetadata",
]
