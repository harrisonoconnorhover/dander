"""Azure stage-zero, Terraform, and read-only verification CLI commands."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer
from click import ClickException
from rich.console import Console

from dander.bootstrap import (
    AzureAdministrativeBootstrap,
    AzureAdministrativeBootstrapError,
    AzureTerraformBootstrap,
    AzureTerraformBootstrapError,
)
from dander.project import ProjectConfigError, load_project_config
from dander.providers.azure_container_apps import (
    AzureDeploymentBinding,
    AzureDeploymentVerificationError,
    AzureDeploymentVerifier,
)

_DEFAULT_AZURE_INFRA_DIR = Path("infra/azure")
_DEFAULT_AZURE_BOOTSTRAP_ADMIN_DIR = Path("infra/azure/bootstrap-admin")
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")
console = Console()
azure_app = typer.Typer(help="Operate and verify manifest-bound Azure Container Apps Jobs.")


@azure_app.command("verify")
def azure_verify(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    expected_image: str = typer.Option(..., "--expected-image"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Verify one deployed Azure pipeline through read-only provider checks."""
    try:
        binding = AzureDeploymentBinding.from_project(
            config=config,
            deployment=deployment,
            pipeline_id=pipeline,
            name=name,
        )
        verification = AzureDeploymentVerifier(binding).verify(expected_image=expected_image)
    except AzureDeploymentVerificationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=verification.as_dict())


def init_azure_admin_plan(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    location: str = typer.Option("eastus", "--location"),
    resource_group_name: str = typer.Option(..., "--resource-group"),
    storage_account_name: str = typer.Option(..., "--state-storage-account"),
    state_container_name: str = typer.Option("tfstate", "--state-container"),
    state_allowed_ip_rule: str = typer.Option(..., "--state-allowed-ip"),
    state_key: str = typer.Option("dander/azure/bootstrap-admin/terraform.tfstate", "--state-key"),
    acr_name: str = typer.Option(..., "--acr-name"),
    managed_identity_name: str = typer.Option(..., "--managed-identity-name"),
    operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ...,
        "--operator-artifact-dir",
        help="Secured directory outside the repository for plans, state, and Terraform metadata.",
    ),
    infra_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_AZURE_BOOTSTRAP_ADMIN_DIR, hidden=True
    ),
) -> None:
    """Plan Azure state, registry, and managed-identity prerequisites without applying."""
    arguments = {
        "subscription_id": subscription_id,
        "location": location,
        "resource_group_name": resource_group_name,
        "storage_account_name": storage_account_name,
        "state_container_name": state_container_name,
        "state_allowed_ip_rule": state_allowed_ip_rule,
        "state_key": state_key,
        "acr_name": acr_name,
        "managed_identity_name": managed_identity_name,
    }
    try:
        plan_path = AzureAdministrativeBootstrap(infra_dir, operator_artifact_dir).execute(
            **arguments
        )
    except AzureAdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    workspace = operator_artifact_dir.expanduser().resolve() / "terraform-workspace"
    console.print(f"[green]Azure administrative bootstrap planned.[/green] Saved plan: {plan_path}")
    console.print(
        "Review: "
        + shlex.join(("terraform", f"-chdir={workspace}", "show", "-no-color", str(plan_path))),
        soft_wrap=True,
    )
    console.print(
        "No Azure resources were created. Provider registration and apply require separate "
        "explicit cost approval."
    )
    console.print(
        "Next after review and approval: "
        + shlex.join(
            (
                "dander",
                "init-azure-admin-apply",
                "--subscription-id",
                subscription_id,
                "--location",
                location,
                "--resource-group",
                resource_group_name,
                "--state-storage-account",
                storage_account_name,
                "--state-container",
                state_container_name,
                "--state-allowed-ip",
                state_allowed_ip_rule,
                "--state-key",
                state_key,
                "--acr-name",
                acr_name,
                "--managed-identity-name",
                managed_identity_name,
                "--operator-artifact-dir",
                str(operator_artifact_dir),
            )
        ),
        soft_wrap=True,
    )


def init_azure_admin_apply(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    location: str = typer.Option("eastus", "--location"),
    resource_group_name: str = typer.Option(..., "--resource-group"),
    storage_account_name: str = typer.Option(..., "--state-storage-account"),
    state_container_name: str = typer.Option("tfstate", "--state-container"),
    state_allowed_ip_rule: str = typer.Option(..., "--state-allowed-ip"),
    state_key: str = typer.Option("dander/azure/bootstrap-admin/terraform.tfstate", "--state-key"),
    acr_name: str = typer.Option(..., "--acr-name"),
    managed_identity_name: str = typer.Option(..., "--managed-identity-name"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_AZURE_BOOTSTRAP_ADMIN_DIR, hidden=True
    ),
) -> None:
    """Apply the reviewed Azure stage-zero plan and migrate state into Azure Storage."""
    if not typer.confirm(
        f"Apply the reviewed Azure stage-zero plan to subscription {subscription_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        plan_path = AzureAdministrativeBootstrap(infra_dir, operator_artifact_dir).apply_saved_plan(
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
    except AzureAdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    workspace = operator_artifact_dir.expanduser().resolve() / "terraform-workspace"
    console.print(f"[green]Azure administrative bootstrap applied.[/green] Saved plan: {plan_path}")
    console.print(
        "Record the generated launcher client id with: "
        + shlex.join(
            (
                "terraform",
                f"-chdir={workspace}",
                "output",
                "-raw",
                "runtime_identity_client_id",
            )
        ),
        soft_wrap=True,
    )


def init_azure_plan(
    state_resource_group_name: str = typer.Option(..., "--state-resource-group"),
    state_storage_account_name: str = typer.Option(..., "--state-storage-account"),
    state_container_name: str = typer.Option("tfstate", "--state-container"),
    state_key: str = typer.Option("dander/azure/state/terraform.tfstate", "--state-key"),
    container_image: str = typer.Option(
        ..., "--container-image", help="Immutable ACR image ending in @sha256 digest."
    ),
    deployment: str = typer.Option(..., "--deployment"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    key_vault_allowed_ip_rule: str = typer.Option(..., "--key-vault-allowed-ip"),
    alert_target: str | None = typer.Option(None, "--alert-action-group-id"),
    infrastructure_subnet_id: str | None = typer.Option(None, "--infrastructure-subnet-id"),
    name: str = typer.Option("dander", "--name"),
    infra_dir: Path = typer.Option(_DEFAULT_AZURE_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan one manifest-selected Azure deployment without applying it."""
    try:
        manifest = load_project_config(
            config,
            platforms_path=platforms_config,
            deployment=deployment,
        )
        if manifest.launcher_provider != "azure_container_apps":
            raise ProjectConfigError(
                f"Deployment {deployment!r} does not select "
                "launcher.provider='azure_container_apps'"
            )
        manifest.validate_references(config.resolve().parent)
        runtime = manifest.platform.runtime
        plan_path = AzureTerraformBootstrap(infra_dir).execute(
            deployment_name=manifest.deployment_name,
            state_resource_group_name=state_resource_group_name,
            state_storage_account_name=state_storage_account_name,
            state_container_name=state_container_name,
            state_key=state_key,
            container_image=container_image,
            launcher_config=manifest.resolved_launcher_config(),
            key_vault_allowed_ip_rule=key_vault_allowed_ip_rule,
            runtime_cpu=runtime.cpu,
            runtime_memory=runtime.memory,
            runtime_timeout_seconds=runtime.timeout_seconds,
            runtime_max_retries=runtime.max_retries,
            runtime_batch_rows=runtime.batch_rows,
            require_guarded_free_tier=manifest.platform.safety.require_guarded_free_tier,
            pipelines=manifest.terraform_pipelines(),
            apply=False,
            alert_target=alert_target,
            infrastructure_subnet_id=infrastructure_subnet_id,
            name=name,
        )
    except (AzureTerraformBootstrapError, ProjectConfigError) as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Azure deployment planned.[/green] Saved plan: {plan_path}")
    console.print(
        "Review: "
        + shlex.join(("terraform", f"-chdir={infra_dir}", "show", "-no-color", str(plan_path))),
        soft_wrap=True,
    )
    console.print("No Azure resources were changed. Apply requires separate explicit approval.")
    subscription_id = str(manifest.resolved_launcher_config()["subscription_id"])
    console.print(
        "Next after review and approval: "
        + shlex.join(
            (
                "dander",
                "init-azure-apply",
                "--subscription-id",
                subscription_id,
                "--state-resource-group",
                state_resource_group_name,
                "--state-storage-account",
                state_storage_account_name,
                "--state-container",
                state_container_name,
                "--state-key",
                state_key,
            )
        ),
        soft_wrap=True,
    )


def init_azure_apply(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    state_resource_group_name: str = typer.Option(..., "--state-resource-group"),
    state_storage_account_name: str = typer.Option(..., "--state-storage-account"),
    state_container_name: str = typer.Option("tfstate", "--state-container"),
    state_key: str = typer.Option("dander/azure/state/terraform.tfstate", "--state-key"),
    infra_dir: Path = typer.Option(_DEFAULT_AZURE_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply only the previously reviewed Azure Terraform plan."""
    if not typer.confirm(
        f"Apply the reviewed Azure deployment plan to subscription {subscription_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        plan_path = AzureTerraformBootstrap(infra_dir).apply_saved_plan(
            subscription_id=subscription_id,
            state_resource_group_name=state_resource_group_name,
            state_storage_account_name=state_storage_account_name,
            state_container_name=state_container_name,
            state_key=state_key,
        )
    except AzureTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Azure deployment applied.[/green] Saved plan: {plan_path}")


def register_azure_commands(app: typer.Typer) -> None:
    """Register Azure operation and compatibility-preserving flat command names."""
    app.add_typer(azure_app, name="azure")
    app.command("init-azure-admin-plan")(init_azure_admin_plan)
    app.command("init-azure-admin-apply")(init_azure_admin_apply)
    app.command("init-azure-plan")(init_azure_plan)
    app.command("init-azure-apply")(init_azure_apply)


__all__ = ["register_azure_commands"]
