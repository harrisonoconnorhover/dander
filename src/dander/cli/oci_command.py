"""OCI stage-zero and provider-foundation CLI commands."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer
from click import ClickException
from rich.console import Console

from dander.bootstrap import (
    OciAdministrativeBootstrap,
    OciTerraformBootstrap,
    OciTerraformBootstrapError,
)

_DEFAULT_OCI_INFRA_DIR = Path("infra/oci")
_DEFAULT_OCI_ADMIN_DIR = Path("infra/oci/bootstrap-admin")
console = Console()


def _admin_arguments(
    *,
    tenancy_id: str,
    compartment_id: str,
    region: str,
    state_bucket_name: str,
    state_key: str,
    repository_name: str,
    config_file_profile: str,
) -> dict[str, str]:
    return {
        "tenancy_id": tenancy_id,
        "compartment_id": compartment_id,
        "region": region,
        "state_bucket_name": state_bucket_name,
        "state_key": state_key,
        "repository_name": repository_name,
        "config_file_profile": config_file_profile,
    }


def init_oci_admin_plan(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/oci/bootstrap-admin/terraform.tfstate", "--state-key"),
    repository_name: str = typer.Option("dander/runtime", "--repository"),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ...,
        "--operator-artifact-dir",
        help="Secured directory outside the repository for plans, state, and Terraform metadata.",
    ),
    infra_dir: Path = typer.Option(_DEFAULT_OCI_ADMIN_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan the OCI state bucket and immutable OCIR repository without applying."""
    arguments = _admin_arguments(
        tenancy_id=tenancy_id,
        compartment_id=compartment_id,
        region=region,
        state_bucket_name=state_bucket_name,
        state_key=state_key,
        repository_name=repository_name,
        config_file_profile=config_file_profile,
    )
    try:
        plan_path = OciAdministrativeBootstrap(infra_dir, operator_artifact_dir).execute(
            **arguments
        )
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    _print_plan("OCI administrative bootstrap", operator_artifact_dir, plan_path)
    console.print("No OCI resources were created. Apply requires explicit cost approval.")


def init_oci_admin_apply(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--object-storage-namespace"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/oci/bootstrap-admin/terraform.tfstate", "--state-key"),
    repository_name: str = typer.Option("dander/runtime", "--repository"),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_OCI_ADMIN_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply the reviewed OCI stage-zero plan and migrate to native remote state."""
    if not typer.confirm(
        f"Apply the reviewed OCI stage-zero plan in tenancy {tenancy_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        plan_path = OciAdministrativeBootstrap(infra_dir, operator_artifact_dir).apply_saved_plan(
            namespace=namespace,
            **_admin_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                state_bucket_name=state_bucket_name,
                state_key=state_key,
                repository_name=repository_name,
                config_file_profile=config_file_profile,
            ),
        )
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]OCI administrative bootstrap applied.[/green] Saved plan: {plan_path}")


def init_oci_plan(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--object-storage-namespace"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/oci/foundation/terraform.tfstate", "--state-key"),
    dynamic_group_name: str = typer.Option("dander_phase7_runtime", "--dynamic-group"),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_OCI_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan the private OCI network, Vault, identity, and observability foundation."""
    arguments = _foundation_arguments(
        tenancy_id=tenancy_id,
        compartment_id=compartment_id,
        region=region,
        namespace=namespace,
        state_bucket_name=state_bucket_name,
        state_key=state_key,
        dynamic_group_name=dynamic_group_name,
        config_file_profile=config_file_profile,
        name=name,
    )
    try:
        plan_path = OciTerraformBootstrap(infra_dir, operator_artifact_dir).execute(**arguments)
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    _print_plan("OCI foundation", operator_artifact_dir, plan_path)
    console.print("No OCI resources were changed. Apply requires explicit cost approval.")


def init_oci_apply(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--object-storage-namespace"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/oci/foundation/terraform.tfstate", "--state-key"),
    dynamic_group_name: str = typer.Option("dander_phase7_runtime", "--dynamic-group"),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_OCI_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply only the previously reviewed OCI foundation plan."""
    if not typer.confirm(
        f"Apply the reviewed OCI foundation plan in compartment {compartment_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        plan_path = OciTerraformBootstrap(infra_dir, operator_artifact_dir).apply_saved_plan(
            **_foundation_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                namespace=namespace,
                state_bucket_name=state_bucket_name,
                state_key=state_key,
                dynamic_group_name=dynamic_group_name,
                config_file_profile=config_file_profile,
                name=name,
            )
        )
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]OCI foundation applied.[/green] Saved plan: {plan_path}")


def verify_oci_deployment(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--object-storage-namespace"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    admin_state_key: str = typer.Option(
        "dander/oci/bootstrap-admin/terraform.tfstate", "--admin-state-key"
    ),
    foundation_state_key: str = typer.Option(
        "dander/oci/foundation/terraform.tfstate", "--foundation-state-key"
    ),
    repository_name: str = typer.Option("dander/runtime", "--repository"),
    dynamic_group_name: str = typer.Option("dander_phase7_runtime", "--dynamic-group"),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
    admin_operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ..., "--admin-operator-artifact-dir"
    ),
    foundation_operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ..., "--foundation-operator-artifact-dir"
    ),
    admin_infra_dir: Path = typer.Option(_DEFAULT_OCI_ADMIN_DIR, hidden=True),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_OCI_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Read OCI APIs through Terraform and fail if stage zero or the foundation drifted."""
    try:
        OciAdministrativeBootstrap(admin_infra_dir, admin_operator_artifact_dir).verify_no_drift(
            namespace=namespace,
            **_admin_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                state_bucket_name=state_bucket_name,
                state_key=admin_state_key,
                repository_name=repository_name,
                config_file_profile=config_file_profile,
            ),
        )
        OciTerraformBootstrap(infra_dir, foundation_operator_artifact_dir).verify_no_drift(
            **_foundation_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                namespace=namespace,
                state_bucket_name=state_bucket_name,
                state_key=foundation_state_key,
                dynamic_group_name=dynamic_group_name,
                config_file_profile=config_file_profile,
                name=name,
            )
        )
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print_json(
        data={
            "schema": "io.dander.oci-deployment-verification/v1",
            "status": "no_drift",
            "region": region,
            "state_bucket": state_bucket_name,
            "repository": repository_name,
            "dynamic_group": dynamic_group_name,
            "authentication": "SecurityToken",
        }
    )


def _foundation_arguments(**values: str) -> dict[str, str]:
    return dict(values)


def _print_plan(label: str, operator_artifact_dir: Path, plan_path: Path) -> None:
    workspace = operator_artifact_dir.expanduser().resolve() / "terraform-workspace"
    console.print(f"[green]{label} planned.[/green] Saved plan: {plan_path}")
    console.print(
        "Review: "
        + shlex.join(("terraform", f"-chdir={workspace}", "show", "-no-color", str(plan_path))),
        soft_wrap=True,
    )


def register_oci_commands(app: typer.Typer) -> None:
    """Register OCI plan-first administrative commands."""
    app.command("init-oci-admin-plan")(init_oci_admin_plan)
    app.command("init-oci-admin-apply")(init_oci_admin_apply)
    app.command("init-oci-plan")(init_oci_plan)
    app.command("init-oci-apply")(init_oci_apply)
    app.command("verify-oci-deployment")(verify_oci_deployment)


__all__ = ["register_oci_commands"]
