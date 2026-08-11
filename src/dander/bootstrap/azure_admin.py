"""Plan-first Azure stage-zero bootstrap with post-apply remote-state migration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from ipaddress import IPv4Address, ip_address
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from pathlib import Path

_LOCATION = re.compile(r"^[a-z][a-z0-9]{1,31}$")
_RESOURCE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_STORAGE_ACCOUNT = re.compile(r"^[a-z][a-z0-9]{2,23}$")
_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_ACR_NAME = re.compile(r"^[a-z][a-z0-9]{4,49}$")
_STATE_KEY = re.compile(r"^(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_BACKEND_SCHEMA = "io.dander.azure-bootstrap-backend/v1"


class AzureAdministrativeBootstrapError(RuntimeError):
    """Raised when Azure stage zero cannot be planned or applied safely."""


class AzureAdministrativeBootstrap:
    """Create Azure state, registry, and workload-identity prerequisites through a saved plan."""

    def __init__(self, infra_dir: Path, operator_artifact_dir: Path) -> None:
        self._infra_dir = infra_dir.resolve()
        self._repository_dir = self._infra_dir.parent.parent.parent
        self._operator_artifact_dir = operator_artifact_dir.expanduser().resolve()
        if self._is_within(self._operator_artifact_dir, self._repository_dir):
            raise AzureAdministrativeBootstrapError(
                "Azure operator artifact directory must be outside the repository checkout"
            )
        self._workspace = self._operator_artifact_dir / "terraform-workspace"
        self._tf_data_dir = self._operator_artifact_dir / "terraform-data"
        self._plan_path = self._operator_artifact_dir / "dander-azure-admin-bootstrap.tfplan"
        self._backend_path = self._workspace / "backend.tf.json"
        self._backend_record_path = self._operator_artifact_dir / "backend.json"
        self._local_state_path = self._operator_artifact_dir / "terraform.tfstate"

    def execute(
        self,
        *,
        subscription_id: str,
        location: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_allowed_ip_rule: str,
        state_key: str,
        acr_name: str,
        managed_identity_name: str,
    ) -> Path:
        """Save a stage-zero plan without applying or creating Azure resources."""
        self._validate(
            subscription_id=subscription_id,
            location=location,
            resource_group_name=resource_group_name,
            storage_account_name=storage_account_name,
            state_container_name=state_container_name,
            state_allowed_ip_rule=state_allowed_ip_rule,
            state_key=state_key,
            acr_name=acr_name,
            managed_identity_name=managed_identity_name,
        )
        self._prepare_operator_directories()
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend("local", {"path": str(self._local_state_path)})
        else:
            self._require_matching_backend(
                backend,
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                storage_account_name=storage_account_name,
                state_container_name=state_container_name,
                state_key=state_key,
            )
            self._write_azurerm_backend(
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                storage_account_name=storage_account_name,
                state_container_name=state_container_name,
                state_key=state_key,
            )
        self._run("terraform", "init", "-reconfigure", "-input=false")
        self._run(
            "terraform",
            "plan",
            "-input=false",
            f"-var=subscription_id={subscription_id}",
            f"-var=location={location}",
            f"-var=resource_group_name={resource_group_name}",
            f"-var=storage_account_name={storage_account_name}",
            f"-var=state_container_name={state_container_name}",
            f"-var=state_allowed_ip_rule={state_allowed_ip_rule}",
            f"-var=acr_name={acr_name}",
            f"-var=managed_identity_name={managed_identity_name}",
            f"-out={self._plan_path}",
        )
        self._secure_plan()
        return self._plan_path

    def apply_saved_plan(
        self,
        *,
        subscription_id: str,
        location: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_allowed_ip_rule: str,
        state_key: str,
        acr_name: str,
        managed_identity_name: str,
    ) -> Path:
        """Apply only the reviewed plan, then migrate initial local state into Azure Storage."""
        self._validate(
            subscription_id=subscription_id,
            location=location,
            resource_group_name=resource_group_name,
            storage_account_name=storage_account_name,
            state_container_name=state_container_name,
            state_allowed_ip_rule=state_allowed_ip_rule,
            state_key=state_key,
            acr_name=acr_name,
            managed_identity_name=managed_identity_name,
        )
        self._prepare_operator_directories(preserve_plan=True)
        if not self._plan_path.is_file() or self._plan_path.is_symlink():
            raise AzureAdministrativeBootstrapError(
                f"No reviewed Azure stage-zero plan exists at {self._plan_path}"
            )
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend("local", {"path": str(self._local_state_path)})
        else:
            self._require_matching_backend(
                backend,
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                storage_account_name=storage_account_name,
                state_container_name=state_container_name,
                state_key=state_key,
            )
            self._write_azurerm_backend(
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                storage_account_name=storage_account_name,
                state_container_name=state_container_name,
                state_key=state_key,
            )
        self._run("terraform", "init", "-reconfigure", "-input=false")
        self._run("terraform", "apply", "-input=false", str(self._plan_path))
        if backend is None:
            self._migrate_state(
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                storage_account_name=storage_account_name,
                state_container_name=state_container_name,
                state_key=state_key,
            )
        return self._plan_path

    def _migrate_state(
        self,
        *,
        subscription_id: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_key: str,
    ) -> None:
        self._write_azurerm_backend(
            subscription_id=subscription_id,
            resource_group_name=resource_group_name,
            storage_account_name=storage_account_name,
            state_container_name=state_container_name,
            state_key=state_key,
        )
        try:
            self._run("terraform", "init", "-migrate-state", "-force-copy", "-input=false")
        except AzureAdministrativeBootstrapError:
            self._write_backend("local", {"path": str(self._local_state_path)})
            raise
        self._write_json(
            self._backend_record_path,
            {
                "schema": _BACKEND_SCHEMA,
                "subscription_id": subscription_id,
                "resource_group_name": resource_group_name,
                "storage_account_name": storage_account_name,
                "container_name": state_container_name,
                "key": state_key,
            },
        )

    def _prepare_operator_directories(self, *, preserve_plan: bool = False) -> None:
        try:
            for path, label in (
                (self._operator_artifact_dir, "operator artifact directory"),
                (self._workspace, "Terraform workspace"),
                (self._tf_data_dir, "terraform-data"),
            ):
                if path.is_symlink():
                    raise AzureAdministrativeBootstrapError(f"{label} must not be a symlink")
                if path.exists() and not path.is_dir():
                    raise AzureAdministrativeBootstrapError(f"{label} must be a directory")
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            if self._plan_path.is_symlink():
                raise AzureAdministrativeBootstrapError("Saved Azure plan must not be a symlink")
            if self._plan_path.exists() and not self._plan_path.is_file():
                raise AzureAdministrativeBootstrapError("Saved Azure plan must be a regular file")
            if self._plan_path.exists() and not preserve_plan:
                self._plan_path.unlink()
            for existing in self._workspace.iterdir():
                is_source = existing.suffix == ".tf" or (
                    existing.name.endswith(".tf.json") and existing != self._backend_path
                )
                if not is_source and existing.name != ".terraform.lock.hcl":
                    continue
                if existing.is_symlink() or not existing.is_file():
                    raise AzureAdministrativeBootstrapError(
                        "Terraform workspace files must be regular files, not symlinks"
                    )
                existing.unlink()
            for source in self._infra_dir.iterdir():
                if source.is_file() and (
                    source.suffix == ".tf" or source.name == ".terraform.lock.hcl"
                ):
                    shutil.copy2(source, self._workspace / source.name)
        except OSError as error:
            raise AzureAdministrativeBootstrapError(
                "Could not prepare the Azure operator artifact directory"
            ) from error

    def _secure_plan(self) -> None:
        try:
            if not self._plan_path.is_file() or self._plan_path.is_symlink():
                raise AzureAdministrativeBootstrapError(
                    f"Terraform did not create a regular saved plan at {self._plan_path}"
                )
            self._plan_path.chmod(0o600)
        except OSError as error:
            raise AzureAdministrativeBootstrapError(
                "Could not secure the saved Azure Terraform plan"
            ) from error

    def _load_backend_record(self) -> dict[str, object] | None:
        if not self._backend_record_path.exists():
            return None
        if not self._backend_record_path.is_file() or self._backend_record_path.is_symlink():
            raise AzureAdministrativeBootstrapError("Azure backend record must be a regular file")
        try:
            document = json.loads(self._backend_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AzureAdministrativeBootstrapError("Azure backend record is unreadable") from error
        if not isinstance(document, dict) or document.get("schema") != _BACKEND_SCHEMA:
            raise AzureAdministrativeBootstrapError(
                "Azure backend record has an unsupported schema"
            )
        return document

    @staticmethod
    def _require_matching_backend(
        backend: dict[str, object],
        *,
        subscription_id: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_key: str,
    ) -> None:
        expected = {
            "subscription_id": subscription_id,
            "resource_group_name": resource_group_name,
            "storage_account_name": storage_account_name,
            "container_name": state_container_name,
            "key": state_key,
        }
        if any(backend.get(key) != value for key, value in expected.items()):
            raise AzureAdministrativeBootstrapError(
                "Azure backend inputs do not match the previously migrated stage-zero state"
            )

    def _write_azurerm_backend(
        self,
        *,
        subscription_id: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_key: str,
    ) -> None:
        self._write_backend(
            "azurerm",
            {
                "subscription_id": subscription_id,
                "resource_group_name": resource_group_name,
                "storage_account_name": storage_account_name,
                "container_name": state_container_name,
                "key": state_key,
                "use_azuread_auth": True,
            },
        )

    def _write_backend(self, backend_type: str, config: dict[str, object]) -> None:
        self._write_json(
            self._backend_path,
            {"terraform": {"backend": {backend_type: config}}},
        )

    @staticmethod
    def _write_json(path: Path, document: dict[str, object]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        except OSError as error:
            raise AzureAdministrativeBootstrapError(
                "Could not write Azure backend metadata"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate(
        *,
        subscription_id: str,
        location: str,
        resource_group_name: str,
        storage_account_name: str,
        state_container_name: str,
        state_allowed_ip_rule: str,
        state_key: str,
        acr_name: str,
        managed_identity_name: str,
    ) -> None:
        try:
            parsed = UUID(subscription_id)
        except ValueError as error:
            raise AzureAdministrativeBootstrapError("Invalid Azure subscription id") from error
        if parsed.variant != "specified in RFC 4122":
            raise AzureAdministrativeBootstrapError("Invalid Azure subscription id")
        try:
            address = ip_address(state_allowed_ip_rule)
        except ValueError as error:
            raise AzureAdministrativeBootstrapError(
                "Invalid Azure state allowed IP rule"
            ) from error
        if not isinstance(address, IPv4Address):
            raise AzureAdministrativeBootstrapError("Invalid Azure state allowed IP rule")
        checks = (
            (_LOCATION, location, "location"),
            (_RESOURCE_NAME, resource_group_name, "resource group name"),
            (_STORAGE_ACCOUNT, storage_account_name, "storage account name"),
            (_CONTAINER, state_container_name, "state container name"),
            (_STATE_KEY, state_key, "state key"),
            (_ACR_NAME, acr_name, "ACR name"),
            (_RESOURCE_NAME, managed_identity_name, "managed identity name"),
        )
        for pattern, value, label in checks:
            if pattern.fullmatch(value) is None:
                raise AzureAdministrativeBootstrapError(f"Invalid Azure {label}: {value!r}")

    def _run(self, *args: str) -> None:
        environment = os.environ.copy()
        environment["TF_DATA_DIR"] = str(self._tf_data_dir)
        try:
            subprocess.run(
                args,
                cwd=self._workspace,
                check=True,
                env=environment,
                umask=0o077,
            )
        except FileNotFoundError as error:
            raise AzureAdministrativeBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            command = " ".join(args[:2])
            raise AzureAdministrativeBootstrapError(
                f"{command} failed with exit code {error.returncode}"
            ) from error

    @staticmethod
    def _is_within(candidate: Path, parent: Path) -> bool:
        try:
            candidate.relative_to(parent)
        except ValueError:
            return False
        return True


__all__ = ["AzureAdministrativeBootstrap", "AzureAdministrativeBootstrapError"]
