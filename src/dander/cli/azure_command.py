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
    AzureRuntimeImagePromoter,
    AzureTerraformBootstrap,
    AzureTerraformBootstrapError,
    ProjectBootstrapError,
)
from dander.project import ProjectConfigError, load_project_config
from dander.providers.azure_container_apps import (
    AzureContainerAppsOperationError,
    AzureContainerAppsOperations,
    AzureDeploymentBinding,
    AzureDeploymentVerificationError,
    AzureDeploymentVerifier,
)

_DEFAULT_AZURE_INFRA_DIR = Path("infra/azure")
_DEFAULT_AZURE_BOOTSTRAP_ADMIN_DIR = Path("infra/azure/bootstrap-admin")
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")
console = Console()
azure_app = typer.Typer(help="Operate and verify manifest-bound Azure Container Apps Jobs.")


def _azure_operations(
    *,
    config: Path,
    deployment: str,
    pipeline: str,
    name: str,
) -> AzureContainerAppsOperations:
    binding = AzureDeploymentBinding.from_project(
        config=config,
        deployment=deployment,
        pipeline_id=pipeline,
        name=name,
    )
    return AzureContainerAppsOperations(binding)


@azure_app.command("run")
def azure_run(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Start one potentially billable Container Apps Job execution after confirmation."""
    if not typer.confirm(f"Start Azure Container Apps pipeline {pipeline!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        ).start()
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@azure_app.command("identity-refresh-probe")
def azure_identity_refresh_probe(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    project: str = typer.Option(..., "--project"),
    dataset: str = typer.Option(..., "--dataset"),
    table: str = typer.Option(..., "--table"),
    max_wait_seconds: int = typer.Option(900, "--max-wait-seconds", min=1, max=1_800),
    refresh_margin_seconds: int = typer.Option(15, "--refresh-margin-seconds", min=0, max=60),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Start one potentially billable Azure-to-Google credential refresh proof."""
    try:
        manifest = load_project_config(config, deployment=deployment)
        if (
            manifest.launcher_provider != "azure_container_apps"
            or manifest.warehouse_provider != "bigquery"
            or manifest.state_provider != "bigquery"
            or manifest.catalog_provider != "dataplex"
            or manifest.secret_provider != "gcp_secret_manager"
        ):
            raise ProjectConfigError(
                "Identity refresh probe requires the named Azure BigQuery federation profile"
            )
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    if not typer.confirm(
        "Start one paid Azure-to-Google identity refresh proof with no automatic rerun?",
        default=False,
    ):
        raise typer.Abort()
    try:
        execution = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        ).start_identity_refresh_probe(
            project=project,
            dataset=dataset,
            table=table,
            max_wait_seconds=max_wait_seconds,
            refresh_margin_seconds=refresh_margin_seconds,
        )
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@azure_app.command("status")
def azure_status(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_name: str | None = typer.Option(None, "--execution-name"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Show sanitized status for one execution, or the latest execution."""
    try:
        operations = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        )
        execution = (
            operations.describe(execution_name)
            if execution_name is not None
            else operations.latest()
        )
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data={"execution": execution.as_dict() if execution is not None else None})


@azure_app.command("logs")
def azure_logs(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_name: str = typer.Option(..., "--execution-name"),
    limit: int = typer.Option(100, "--limit", min=1, max=10_000),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Read bounded Log Analytics events for one exact execution."""
    try:
        events = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        ).logs(execution_name, limit=limit)
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data={"events": [event.as_dict() for event in events]})


@azure_app.command("cancel")
def azure_cancel(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_name: str = typer.Option(..., "--execution-name"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Stop one running Container Apps Job execution after confirmation."""
    if not typer.confirm(f"Stop Azure execution {execution_name!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        ).cancel(execution_name)
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@azure_app.command("replay")
def azure_replay(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_name: str = typer.Option(..., "--execution-name"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Replay a terminal execution from the runtime's persisted inclusive cursor."""
    if not typer.confirm(f"Replay Azure Container Apps pipeline {pipeline!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _azure_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
        ).replay(execution_name)
    except (AzureContainerAppsOperationError, AzureDeploymentVerificationError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


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


@azure_app.command("canonical-preflight")
def azure_canonical_preflight(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    expected_image: str = typer.Option(..., "--expected-image"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Verify the named Azure/Snowflake/PostgreSQL/Key Vault profile read-only."""
    try:
        manifest = load_project_config(config, deployment=deployment)
        if (
            manifest.launcher_provider != "azure_container_apps"
            or manifest.warehouse_provider != "snowflake"
            or manifest.state_provider != "postgresql"
            or manifest.catalog_provider != "none"
            or manifest.secret_provider != "azure_key_vault"
        ):
            raise ProjectConfigError(
                "Canonical preflight requires the named Azure/Snowflake/PostgreSQL/"
                "no-catalog/Key-Vault profile"
            )
        snowflake_auth = manifest.warehouse_config.get("auth")
        postgres_dsn_env = manifest.state_config.get("dsn_env")
        if (
            not isinstance(snowflake_auth, dict)
            or snowflake_auth.get("method") != "oauth"
            or not isinstance(token_env := snowflake_auth.get("token_env"), str)
            or not isinstance(postgres_dsn_env, str)
        ):
            raise ProjectConfigError(
                "Canonical preflight requires Snowflake OAuth and PostgreSQL DSN references"
            )
        try:
            secret_environment = set(manifest.pipelines[pipeline].secrets)
        except KeyError as error:
            raise ProjectConfigError(
                f"Pipeline {pipeline!r} is not declared in the project manifest"
            ) from error
        if not {token_env, postgres_dsn_env}.issubset(secret_environment):
            raise ProjectConfigError(
                "Canonical preflight requires pipeline bindings for Snowflake OAuth and "
                "PostgreSQL DSN secrets"
            )
        binding = AzureDeploymentBinding.from_project(
            config=config,
            deployment=deployment,
            pipeline_id=pipeline,
            name=name,
        )
        verifier = AzureDeploymentVerifier(binding)
        verification = verifier.verify(expected_image=expected_image)
        secret_metadata = verifier.verify_declared_secret_metadata()
    except (AzureDeploymentVerificationError, ProjectConfigError) as error:
        raise ClickException(str(error)) from error
    console.print_json(
        data={
            "deployment": verification.as_dict(),
            "declared_secrets": [metadata.as_dict() for metadata in secret_metadata],
        }
    )


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


def image_promote_azure(
    source_image: str = typer.Option(
        ..., "--source-image", help="Accepted source-free OCI image ending in @sha256 digest."
    ),
    subscription_id: str = typer.Option(..., "--subscription-id"),
    acr_name: str = typer.Option(..., "--acr-name"),
    repository_name: str = typer.Option("dander/runtime", "--acr-repository"),
    tag_prefix: str = typer.Option("promoted", "--tag-prefix"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
) -> None:
    """Copy an accepted source-free OCI index into ACR without rebuilding it."""
    if not typer.confirm(
        f"Copy the accepted runtime image into Azure Container Registry {acr_name!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        promoter = AzureRuntimeImagePromoter(config.resolve().parent)
        image = promoter.promote(
            source_image=source_image,
            subscription_id=subscription_id,
            acr_name=acr_name,
            repository_name=repository_name,
            tag_prefix=tag_prefix,
        )
    except ProjectBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Promoted byte-identical runtime image:[/green] {image}")
    if promoter.artifact_record_path is not None:
        console.print(f"Azure artifact record: {promoter.artifact_record_path}")
    console.print("Next: use this immutable digest with dander init-azure-plan.")


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
    infrastructure_subnet_id: str | None = typer.Option(
        None,
        "--infrastructure-subnet-id",
        help=(
            "Existing delegated Container Apps subnet; required for Azure Key Vault references "
            "and expected to have the Microsoft.KeyVault service endpoint."
        ),
    ),
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
        gcp_project = None
        if manifest.warehouse_provider == "bigquery":
            project_value = manifest.warehouse_config.get("project")
            if not isinstance(project_value, str):
                raise ProjectConfigError("Azure BigQuery profile has no GCP project")
            gcp_project = project_value
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
            profile_id=manifest.platform_name,
            gcp_project=gcp_project,
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
    app.command("image-promote-azure")(image_promote_azure)
    app.command("init-azure-plan")(init_azure_plan)
    app.command("init-azure-apply")(init_azure_apply)


__all__ = ["register_azure_commands"]
