"""OCI stage-zero and provider-foundation CLI commands."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, cast

import typer
from click import ClickException
from rich.console import Console

from dander.bootstrap import (
    OciAdministrativeBootstrap,
    OciControllerImagePublisher,
    OciRuntimeImagePromoter,
    OciTerraformBootstrap,
    OciTerraformBootstrapError,
    ProjectBootstrapError,
    build_oci_execution_projections,
)
from dander.project import ProjectConfigError, load_project_config
from dander.providers.oci_container_instances import (
    OciContainerInstanceOperations,
    OciLifecycleError,
    OciOperationBinding,
    OciOperationError,
)

_DEFAULT_OCI_INFRA_DIR = Path("infra/oci")
_DEFAULT_OCI_ADMIN_DIR = Path("infra/oci/bootstrap-admin")
console = Console()
oci_app = typer.Typer(help="Operate manifest-bound OCI Container Instances pipelines.")


def image_promote_oci(
    source_image: str = typer.Option(
        ..., "--source-image", help="Accepted source-free OCI image ending in @sha256 digest."
    ),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--registry-namespace"),
    repository_name: str = typer.Option("dander/runtime", "--repository"),
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    tag_prefix: str = typer.Option("promoted", "--tag-prefix"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
) -> None:
    """Copy an accepted OCI index into OCIR using one short-lived scoped token."""
    if not typer.confirm(
        f"Copy the accepted runtime image into OCI repository {repository_name!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        promoter = OciRuntimeImagePromoter(config.resolve().parent)
        image = promoter.promote(
            source_image=source_image,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            repository_name=repository_name,
            oci_profile=oci_profile,
            tag_prefix=tag_prefix,
        )
    except ProjectBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Promoted byte-identical runtime image:[/green] {image}")
    if promoter.artifact_record_path is not None:
        console.print(f"OCI artifact record: {promoter.artifact_record_path}")
    console.print("Next: use this immutable digest with dander init-oci-launcher-plan.")


def image_publish_oci_controller(
    wheel: Path = typer.Option(  # noqa: B008
        ..., "--wheel", help="Exact reviewed Dander wheel."
    ),
    wheel_sha256: str = typer.Option(..., "--wheel-sha256"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--registry-namespace"),
    repository_name: str = typer.Option("dander/runtime", "--repository"),
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
) -> None:
    """Build the OCI lifecycle controller solely from an exact reviewed wheel."""
    if not typer.confirm(
        f"Publish the wheel-bound OCI controller into repository {repository_name!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        publisher = OciControllerImagePublisher(config.resolve().parent)
        image = publisher.publish(
            wheel=wheel,
            wheel_sha256=wheel_sha256,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            repository_name=repository_name,
            oci_profile=oci_profile,
        )
    except ProjectBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Published immutable OCI controller image:[/green] {image}")
    if publisher.artifact_record_path is not None:
        console.print(f"OCI controller artifact record: {publisher.artifact_record_path}")
    console.print("Next: pass this digest-qualified image to dander init-oci-launcher-plan.")


def _oci_operations(
    *,
    config: Path,
    deployment: str,
    pipeline: str,
    function_id: str,
    oci_profile: str,
    name: str,
) -> OciContainerInstanceOperations:
    binding = OciOperationBinding.from_project(
        config=config,
        deployment=deployment,
        pipeline_id=pipeline,
        function_id=function_id,
        name=name,
    )
    return OciContainerInstanceOperations.from_security_token_profile(
        binding,
        profile=oci_profile,
    )


def _operation(
    *,
    config: Path,
    deployment: str,
    pipeline: str,
    function_id: str,
    oci_profile: str,
    name: str,
) -> OciContainerInstanceOperations:
    try:
        return _oci_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        )
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error


@oci_app.command("run")
def oci_run(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    function_id: str = typer.Option(..., "--function-id"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Start one potentially billable Container Instance after confirmation."""
    if not typer.confirm(f"Start OCI Container Instances pipeline {pipeline!r}?", default=False):
        raise typer.Abort()
    try:
        invocation = _operation(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        ).start()
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=invocation.as_dict())


@oci_app.command("status")
def oci_status(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    function_id: str = typer.Option(..., "--function-id"),
    run_id: str | None = typer.Option(None, "--run-id"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Describe one exact execution, or the currently active execution."""
    try:
        operations = _operation(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        )
        execution = operations.describe(run_id) if run_id is not None else operations.latest()
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=None if execution is None else execution.as_dict())


@oci_app.command("logs")
def oci_logs(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    function_id: str = typer.Option(..., "--function-id"),
    run_id: str = typer.Option(..., "--run-id"),
    attempt: int | None = typer.Option(None, "--attempt", min=1, max=11),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Read the bounded output retained for one execution attempt."""
    try:
        content = _operation(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        ).logs(run_id, attempt=attempt)
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error
    console.print(content.decode("utf-8", errors="replace"), markup=False, end="")


@oci_app.command("cancel")
def oci_cancel(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    function_id: str = typer.Option(..., "--function-id"),
    run_id: str = typer.Option(..., "--run-id"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Stop and delete one active run after confirmation."""
    if not typer.confirm(f"Cancel OCI execution {run_id!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _operation(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        ).cancel(run_id)
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@oci_app.command("replay")
def oci_replay(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    function_id: str = typer.Option(..., "--function-id"),
    run_id: str = typer.Option(..., "--run-id"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    oci_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Replay one terminal run with a fresh caller-known identity."""
    if not typer.confirm(f"Replay OCI execution {run_id!r}?", default=False):
        raise typer.Abort()
    try:
        invocation = _operation(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            function_id=function_id,
            oci_profile=oci_profile,
            name=name,
        ).replay(run_id)
    except (OciOperationError, OciLifecycleError) as error:
        raise ClickException(str(error)) from error
    console.print_json(data=invocation.as_dict())


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
        plan_path = OciTerraformBootstrap(infra_dir, operator_artifact_dir).execute(
            **cast("Any", arguments)
        )
    except OciTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    _print_plan("OCI foundation", operator_artifact_dir, plan_path)
    console.print("No OCI resources were changed. Apply requires explicit cost approval.")


def init_oci_launcher_plan(
    tenancy_id: str = typer.Option(..., "--tenancy-id"),
    compartment_id: str = typer.Option(..., "--compartment-id"),
    region: str = typer.Option("us-ashburn-1", "--region"),
    namespace: str = typer.Option(..., "--object-storage-namespace"),
    state_bucket_name: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/oci/foundation/terraform.tfstate", "--state-key"),
    container_image: str = typer.Option(..., "--container-image"),
    controller_image: str = typer.Option(..., "--controller-image"),
    controller_image_digest: str = typer.Option(..., "--controller-image-digest"),
    deployment: str = typer.Option(..., "--deployment"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    dynamic_group_name: str = typer.Option("dander_phase7_runtime", "--dynamic-group"),
    controller_dynamic_group_name: str = typer.Option(
        "dander_phase7_controller", "--controller-dynamic-group"
    ),
    scheduler_dynamic_group_name: str = typer.Option(
        "dander_phase7_scheduler", "--scheduler-dynamic-group"
    ),
    config_file_profile: str = typer.Option("DEFAULT", "--oci-profile"),
    name: str = typer.Option("dander", "--name"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_OCI_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan one manifest-selected OCI launcher and its keyless lifecycle controller."""
    try:
        projections = _deployment_projections(
            config=config,
            platforms_config=platforms_config,
            deployment=deployment,
            container_image=container_image,
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            controller_image=controller_image,
        )
        arguments = cast(
            "dict[str, object]",
            _foundation_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                namespace=namespace,
                state_bucket_name=state_bucket_name,
                state_key=state_key,
                dynamic_group_name=dynamic_group_name,
                config_file_profile=config_file_profile,
                name=name,
            ),
        )
        arguments.update(
            {
                "controller_image": controller_image,
                "controller_image_digest": controller_image_digest,
                "execution_projections": projections,
                "controller_dynamic_group_name": controller_dynamic_group_name,
                "scheduler_dynamic_group_name": scheduler_dynamic_group_name,
            }
        )
        plan_path = OciTerraformBootstrap(infra_dir, operator_artifact_dir).execute(
            **cast("Any", arguments)
        )
    except (OciTerraformBootstrapError, ProjectConfigError) as error:
        raise ClickException(str(error)) from error
    _print_plan("OCI launcher", operator_artifact_dir, plan_path)
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
    container_image: str | None = typer.Option(None, "--container-image"),
    controller_image: str | None = typer.Option(None, "--controller-image"),
    controller_image_digest: str | None = typer.Option(None, "--controller-image-digest"),
    deployment: str | None = typer.Option(None, "--deployment"),
    config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    controller_dynamic_group_name: str = typer.Option(
        "dander_phase7_controller", "--controller-dynamic-group"
    ),
    scheduler_dynamic_group_name: str = typer.Option(
        "dander_phase7_scheduler", "--scheduler-dynamic-group"
    ),
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
        controller_values = (container_image, controller_image, controller_image_digest, deployment)
        if any(value is not None for value in controller_values) and not all(
            value is not None for value in controller_values
        ):
            raise OciTerraformBootstrapError(
                "OCI launcher verification requires deployment, runtime image, controller image, "
                "and controller digest together"
            )
        controller_arguments: dict[str, object] = {}
        if deployment is not None:
            assert container_image is not None
            assert controller_image is not None
            assert controller_image_digest is not None
            controller_arguments = {
                "controller_image": controller_image,
                "controller_image_digest": controller_image_digest,
                "execution_projections": _deployment_projections(
                    config=config,
                    platforms_config=platforms_config,
                    deployment=deployment,
                    container_image=container_image,
                    tenancy_id=tenancy_id,
                    compartment_id=compartment_id,
                    region=region,
                    namespace=namespace,
                    controller_image=controller_image,
                ),
                "controller_dynamic_group_name": controller_dynamic_group_name,
                "scheduler_dynamic_group_name": scheduler_dynamic_group_name,
            }
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
        verification_arguments = cast(
            "dict[str, object]",
            _foundation_arguments(
                tenancy_id=tenancy_id,
                compartment_id=compartment_id,
                region=region,
                namespace=namespace,
                state_bucket_name=state_bucket_name,
                state_key=foundation_state_key,
                dynamic_group_name=dynamic_group_name,
                config_file_profile=config_file_profile,
                name=name,
            ),
        )
        verification_arguments.update(controller_arguments)
        OciTerraformBootstrap(infra_dir, foundation_operator_artifact_dir).verify_no_drift(
            **cast("Any", verification_arguments)
        )
    except (OciTerraformBootstrapError, ProjectConfigError) as error:
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


def _deployment_projections(
    *,
    config: Path,
    platforms_config: Path | None,
    deployment: str,
    container_image: str,
    tenancy_id: str,
    compartment_id: str,
    region: str,
    namespace: str,
    controller_image: str,
) -> dict[str, dict[str, object]]:
    manifest = load_project_config(
        config,
        platforms_path=platforms_config,
        deployment=deployment,
    )
    if manifest.launcher_provider != "oci_container_instances":
        raise ProjectConfigError(
            f"Deployment {deployment!r} does not select launcher.provider='oci_container_instances'"
        )
    manifest.validate_references(config.resolve().parent)
    launcher = manifest.resolved_launcher_config()
    expected = {
        "tenancy_id": tenancy_id,
        "compartment_id": compartment_id,
        "region": region,
        "registry_namespace": namespace,
    }
    if any(launcher.get(key) != value for key, value in expected.items()):
        raise ProjectConfigError(
            "OCI manifest tenancy, compartment, region, and registry namespace must match "
            "the selected Terraform foundation"
        )
    repository_name = launcher.get("repository_name")
    expected_controller_repository = (
        f"ocir.{region}.oci.oraclecloud.com/{namespace}/{repository_name}:"
    )
    if not isinstance(repository_name, str) or not controller_image.startswith(
        expected_controller_repository
    ):
        raise ProjectConfigError(
            "OCI controller image must use a unique tag in the manifest-selected OCIR repository"
        )
    runtime = manifest.platform.runtime
    return build_oci_execution_projections(
        container_image=container_image,
        launcher_config=launcher,
        profile_id=deployment,
        runtime_cpu=runtime.cpu,
        runtime_memory=runtime.memory,
        runtime_timeout_seconds=runtime.timeout_seconds,
        runtime_max_retries=runtime.max_retries,
        runtime_batch_rows=runtime.batch_rows,
        require_guarded_free_tier=manifest.platform.safety.require_guarded_free_tier,
        pipelines=manifest.terraform_pipelines(),
    )


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
    app.add_typer(oci_app, name="oci")
    app.command("image-promote-oci")(image_promote_oci)
    app.command("image-publish-oci-controller")(image_publish_oci_controller)
    app.command("init-oci-admin-plan")(init_oci_admin_plan)
    app.command("init-oci-admin-apply")(init_oci_admin_apply)
    app.command("init-oci-plan")(init_oci_plan)
    app.command("init-oci-launcher-plan")(init_oci_launcher_plan)
    app.command("init-oci-apply")(init_oci_apply)
    app.command("verify-oci-deployment")(verify_oci_deployment)


__all__ = ["register_oci_commands"]
