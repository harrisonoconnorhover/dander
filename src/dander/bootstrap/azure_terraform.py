"""Saved-plan Terraform lifecycle for one manifest-defined Azure deployment."""

from __future__ import annotations

import os
import re
import subprocess
from ipaddress import IPv4Address, ip_address
from json import dumps
from typing import TYPE_CHECKING
from uuid import UUID

from dander.bootstrap.terraform import (
    TerraformBootstrapError,
    build_launcher_runtime,
    validate_runtime_settings,
    validate_terraform_pipelines,
)
from dander.deployment import ExecutionProjectionError, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_LOCATION = re.compile(r"^[a-z][a-z0-9]{1,31}$")
_DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_RESOURCE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_STORAGE_ACCOUNT = re.compile(r"^[a-z][a-z0-9]{2,23}$")
_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_STATE_KEY = re.compile(r"^(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_ACR_NAME = re.compile(r"^[a-z][a-z0-9]{4,49}$")
_KEY_VAULT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_ACTION_GROUP_ID = re.compile(
    r"^/subscriptions/(?P<subscription>[0-9a-fA-F-]{36})/resourceGroups/[^/]+/"
    r"providers/[Mm]icrosoft\.[Ii]nsights/actionGroups/[^/]+$"
)
_SUBNET_ID = re.compile(
    r"^/subscriptions/(?P<subscription>[0-9a-fA-F-]{36})/resourceGroups/[^/]+/"
    r"providers/[Mm]icrosoft\.[Nn]etwork/virtualNetworks/[^/]+/subnets/[^/]+$"
)
_ACR_IMAGE = re.compile(
    r"^(?P<registry>[a-z][a-z0-9]{4,49})\.azurecr\.io/"
    r"(?P<repository>[A-Za-z0-9._/-]+)@sha256:(?P<digest>[0-9a-f]{64})$"
)


class AzureTerraformBootstrapError(RuntimeError):
    """Raised when the Azure saved-plan lifecycle cannot complete safely."""


class AzureTerraformBootstrap:
    """Plan or apply the packaged Container Apps Jobs root using Azure Storage state."""

    def __init__(self, infra_dir: Path) -> None:
        self._infra_dir = infra_dir.resolve()
        self._plan_path = self._infra_dir / "dander-azure.tfplan"

    def execute(
        self,
        *,
        deployment_name: str,
        state_resource_group_name: str,
        state_storage_account_name: str,
        state_container_name: str,
        state_key: str,
        container_image: str,
        launcher_config: Mapping[str, object],
        key_vault_allowed_ip_rule: str,
        runtime_cpu: int,
        runtime_memory: str,
        runtime_timeout_seconds: int,
        runtime_max_retries: int,
        runtime_batch_rows: int,
        require_guarded_free_tier: bool,
        pipelines: Mapping[str, Mapping[str, object]],
        apply: bool,
        alert_target: str | None = None,
        infrastructure_subnet_id: str | None = None,
        name: str = "dander",
    ) -> Path:
        """Produce one immutable Azure plan and optionally apply that exact plan."""
        raw_launcher = dict(launcher_config)
        subscription_id = self._subscription_id(raw_launcher.get("subscription_id"))
        self._validate_backend(
            state_resource_group_name=state_resource_group_name,
            state_storage_account_name=state_storage_account_name,
            state_container_name=state_container_name,
            state_key=state_key,
        )
        if not _DEPLOYMENT_NAME.fullmatch(name):
            raise AzureTerraformBootstrapError(
                "Azure deployment name must contain 3-24 lowercase letters, numbers, or hyphens"
            )
        if not deployment_name.strip():
            raise AzureTerraformBootstrapError("Manifest deployment name must not be blank")
        try:
            key_vault_address = ip_address(key_vault_allowed_ip_rule)
        except ValueError as error:
            raise AzureTerraformBootstrapError("Invalid Azure Key Vault allowed IP rule") from error
        if not isinstance(key_vault_address, IPv4Address):
            raise AzureTerraformBootstrapError("Invalid Azure Key Vault allowed IP rule")
        expanded_pipelines = dict(pipelines)
        if not expanded_pipelines:
            raise AzureTerraformBootstrapError("Azure planning requires at least one pipeline")
        try:
            validate_runtime_settings(
                cpu=runtime_cpu,
                memory=runtime_memory,
                timeout_seconds=runtime_timeout_seconds,
                max_retries=runtime_max_retries,
                batch_rows=runtime_batch_rows,
                require_guarded_free_tier=require_guarded_free_tier,
            )
            validate_terraform_pipelines(expanded_pipelines)
        except TerraformBootstrapError as error:
            raise AzureTerraformBootstrapError(str(error)) from error
        if raw_launcher.get("provider") != "azure_container_apps":
            raise AzureTerraformBootstrapError(
                "Azure planning requires launcher.provider='azure_container_apps'"
            )
        location = self._required(raw_launcher, "region", _LOCATION)
        resource_group_name = self._required(raw_launcher, "resource_group_name", _RESOURCE_NAME)
        environment_name = self._required(
            raw_launcher, "container_app_environment_name", _RESOURCE_NAME
        )
        acr_name = self._required(raw_launcher, "acr_name", _ACR_NAME)
        key_vault_name = self._required(raw_launcher, "key_vault_name", _KEY_VAULT_NAME)
        identity_name = self._required(raw_launcher, "managed_identity_name", _RESOURCE_NAME)
        image_match = _ACR_IMAGE.fullmatch(container_image)
        if image_match is None or image_match.group("registry") != acr_name:
            raise AzureTerraformBootstrapError(
                "Azure requires an immutable image in the selected ACR"
            )
        alert_match = _ACTION_GROUP_ID.fullmatch(alert_target or "")
        if alert_target is not None and (
            alert_match is None
            or alert_match.group("subscription").lower() != subscription_id.lower()
        ):
            raise AzureTerraformBootstrapError(
                "Azure alert target must be an Action Group in the selected subscription"
            )
        subnet_match = _SUBNET_ID.fullmatch(infrastructure_subnet_id or "")
        if infrastructure_subnet_id is not None and (
            subnet_match is None
            or subnet_match.group("subscription").lower() != subscription_id.lower()
        ):
            raise AzureTerraformBootstrapError(
                "Azure infrastructure subnet must be a subnet in the selected subscription"
            )
        try:
            launcher = build_launcher_runtime(launcher_config=raw_launcher)
            projections = {
                pipeline_id: template.as_dict()
                for pipeline_id, template in launcher.templates.build(
                    ResolvedTemplateRequest(
                        pipelines=expanded_pipelines,
                        image=container_image,
                        profile_id="azure_snowflake",
                        cpu=runtime_cpu,
                        memory=runtime_memory,
                        deadline_seconds=runtime_timeout_seconds,
                        launcher_retry_count=runtime_max_retries,
                        batch_rows=runtime_batch_rows,
                        alert_target=alert_target,
                    )
                ).items()
            }
        except (ExecutionProjectionError, ProviderFactoryError) as error:
            raise AzureTerraformBootstrapError(str(error)) from error

        self._run(
            "terraform",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=subscription_id={subscription_id}",
            f"-backend-config=resource_group_name={state_resource_group_name}",
            f"-backend-config=storage_account_name={state_storage_account_name}",
            f"-backend-config=container_name={state_container_name}",
            f"-backend-config=key={state_key}",
            "-backend-config=use_azuread_auth=true",
        )
        plan_args = [
            "terraform",
            "plan",
            "-input=false",
            f"-var=subscription_id={subscription_id}",
            f"-var=location={location}",
            f"-var=name={name}",
            f"-var=resource_group_name={resource_group_name}",
            f"-var=container_app_environment_name={environment_name}",
            f"-var=acr_name={acr_name}",
            f"-var=key_vault_name={key_vault_name}",
            f"-var=key_vault_allowed_ip_rule={key_vault_allowed_ip_rule}",
            f"-var=managed_identity_name={identity_name}",
            "-var=execution_projections="
            + dumps(projections, sort_keys=True, separators=(",", ":")),
            "-var=tags="
            + dumps(
                {
                    "dander-deployment": deployment_name,
                    "dander-profile": "azure_snowflake",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            f"-out={self._plan_path.name}",
        ]
        if infrastructure_subnet_id is not None:
            plan_args.insert(-1, f"-var=infrastructure_subnet_id={infrastructure_subnet_id}")
        self._run(*plan_args)
        self._secure_plan()
        if apply:
            self._run("terraform", "apply", "-input=false", self._plan_path.name)
        return self._plan_path

    def apply_saved_plan(
        self,
        *,
        subscription_id: str,
        state_resource_group_name: str,
        state_storage_account_name: str,
        state_container_name: str,
        state_key: str,
    ) -> Path:
        """Apply only the saved plan produced by ``execute``."""
        subscription_id = self._subscription_id(subscription_id)
        self._validate_backend(
            state_resource_group_name=state_resource_group_name,
            state_storage_account_name=state_storage_account_name,
            state_container_name=state_container_name,
            state_key=state_key,
        )
        if not self._plan_path.is_file() or self._plan_path.is_symlink():
            raise AzureTerraformBootstrapError(f"Saved Azure plan is missing: {self._plan_path}")
        self._run(
            "terraform",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=subscription_id={subscription_id}",
            f"-backend-config=resource_group_name={state_resource_group_name}",
            f"-backend-config=storage_account_name={state_storage_account_name}",
            f"-backend-config=container_name={state_container_name}",
            f"-backend-config=key={state_key}",
            "-backend-config=use_azuread_auth=true",
        )
        self._run("terraform", "apply", "-input=false", self._plan_path.name)
        return self._plan_path

    @staticmethod
    def _subscription_id(value: object) -> str:
        if not isinstance(value, str):
            raise AzureTerraformBootstrapError("Azure launcher requires a valid subscription id")
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise AzureTerraformBootstrapError(
                "Azure launcher requires a valid subscription id"
            ) from error
        if parsed.variant != "specified in RFC 4122":
            raise AzureTerraformBootstrapError("Azure launcher requires a valid subscription id")
        return value

    @staticmethod
    def _required(values: Mapping[str, object], key: str, pattern: re.Pattern[str]) -> str:
        value = values.get(key)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise AzureTerraformBootstrapError(f"Azure launcher has invalid {key}")
        return value

    @staticmethod
    def _validate_backend(
        *,
        state_resource_group_name: str,
        state_storage_account_name: str,
        state_container_name: str,
        state_key: str,
    ) -> None:
        checks = (
            (_RESOURCE_NAME, state_resource_group_name, "state resource group"),
            (_STORAGE_ACCOUNT, state_storage_account_name, "state storage account"),
            (_CONTAINER, state_container_name, "state container"),
            (_STATE_KEY, state_key, "state key"),
        )
        for pattern, value, label in checks:
            if pattern.fullmatch(value) is None:
                raise AzureTerraformBootstrapError(f"Invalid Azure {label}: {value!r}")

    def _secure_plan(self) -> None:
        try:
            if not self._plan_path.is_file() or self._plan_path.is_symlink():
                raise AzureTerraformBootstrapError(
                    f"Terraform did not create a regular saved plan at {self._plan_path}"
                )
            self._plan_path.chmod(0o600)
        except OSError as error:
            raise AzureTerraformBootstrapError(
                "Could not secure the saved Azure Terraform plan"
            ) from error

    def _run(self, *args: str) -> None:
        environment = os.environ.copy()
        environment["ARM_USE_AZUREAD"] = "true"
        try:
            subprocess.run(
                args,
                cwd=self._infra_dir,
                check=True,
                env=environment,
                umask=0o077,
            )
        except FileNotFoundError as error:
            raise AzureTerraformBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            command = " ".join(args[:2])
            raise AzureTerraformBootstrapError(
                f"{command} failed with exit code {error.returncode}"
            ) from error


__all__ = ["AzureTerraformBootstrap", "AzureTerraformBootstrapError"]
