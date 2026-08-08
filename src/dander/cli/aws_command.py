"""AWS stage-zero, artifact-promotion, and Fargate Terraform CLI commands."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer
from click import ClickException
from rich.console import Console

from dander.bootstrap import (
    AwsAdministrativeBootstrap,
    AwsAdministrativeBootstrapError,
    AwsTerraformBootstrap,
    AwsTerraformBootstrapError,
    ProjectBootstrapError,
    RuntimeImagePromoter,
)
from dander.project import ProjectConfigError, load_project_config
from dander.providers.fargate import FargateBinding, FargateOperationError, FargateOperations

_DEFAULT_AWS_INFRA_DIR = Path("infra/aws")
_DEFAULT_AWS_BOOTSTRAP_ADMIN_DIR = Path("infra/aws/bootstrap-admin")
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")
console = Console()
aws_app = typer.Typer(help="Operate and verify manifest-bound AWS Fargate pipelines.")


def _fargate_operations(
    *,
    config: Path,
    deployment: str,
    pipeline: str,
    name: str,
    aws_profile: str,
) -> FargateOperations:
    binding = FargateBinding.from_project(
        config=config,
        deployment=deployment,
        pipeline_id=pipeline,
        name=name,
    )
    return FargateOperations(binding, aws_profile=aws_profile)


@aws_app.command("run")
def aws_run(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
    execution_name: str | None = typer.Option(None, "--execution-name"),
) -> None:
    """Start one paid manual Fargate execution after confirmation."""
    if not typer.confirm(f"Start Fargate pipeline {pipeline!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        ).start(execution_name=execution_name)
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@aws_app.command("status")
def aws_status(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_arn: str | None = typer.Option(None, "--execution-arn"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Show sanitized status for one execution, or the latest execution."""
    try:
        operations = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        )
        execution = (
            operations.describe(execution_arn) if execution_arn is not None else operations.latest()
        )
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data={"execution": execution.as_dict() if execution is not None else None})


@aws_app.command("logs")
def aws_logs(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_arn: str = typer.Option(..., "--execution-arn"),
    limit: int = typer.Option(100, "--limit", min=1, max=10_000),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Read CloudWatch events correlated to one controller execution."""
    try:
        events = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        ).logs(execution_arn, limit=limit)
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data={"events": [event.as_dict() for event in events]})


@aws_app.command("cancel")
def aws_cancel(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_arn: str = typer.Option(..., "--execution-arn"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Cancel one running controller execution after confirmation."""
    if not typer.confirm(f"Cancel Fargate execution {execution_arn!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        ).cancel(execution_arn)
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@aws_app.command("replay")
def aws_replay(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    execution_arn: str = typer.Option(..., "--execution-arn"),
    execution_name: str | None = typer.Option(None, "--execution-name"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Replay one terminal pipeline at its inclusive cursor boundary."""
    if not typer.confirm(f"Replay Fargate pipeline {pipeline!r}?", default=False):
        raise typer.Abort()
    try:
        execution = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        ).replay(execution_arn, execution_name=execution_name)
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=execution.as_dict())


@aws_app.command("verify")
def aws_verify(
    deployment: str = typer.Option(..., "--deployment"),
    pipeline: str = typer.Option(..., "--pipeline"),
    expected_image: str = typer.Option(..., "--expected-image"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
) -> None:
    """Verify one deployed Fargate pipeline through read-only provider checks."""
    try:
        verification = _fargate_operations(
            config=config,
            deployment=deployment,
            pipeline=pipeline,
            name=name,
            aws_profile=aws_profile,
        ).verify(expected_image=expected_image)
    except FargateOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=verification.as_dict())


def init_aws_admin_plan(
    aws_account_id: str = typer.Option(..., "--aws-account-id"),
    region: str = typer.Option("us-east-1", "--region"),
    state_bucket: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/aws/bootstrap-admin/terraform.tfstate", "--state-key"),
    lock_table: str = typer.Option("dander-terraform-locks", "--lock-table"),
    ecr_repository_name: str = typer.Option("dander", "--ecr-repository"),
    admin_principal_arn: str = typer.Option(..., "--admin-principal-arn"),
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
    operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ...,
        "--operator-artifact-dir",
        help="Secured directory outside the repository for plans, state, and Terraform metadata.",
    ),
    infra_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_AWS_BOOTSTRAP_ADMIN_DIR, hidden=True
    ),
) -> None:
    """Plan AWS state, registry, and deployment-role prerequisites without applying."""
    try:
        plan_path = AwsAdministrativeBootstrap(infra_dir, operator_artifact_dir).execute(
            aws_account_id=aws_account_id,
            region=region,
            state_bucket=state_bucket,
            state_key=state_key,
            lock_table=lock_table,
            ecr_repository_name=ecr_repository_name,
            admin_principal_arn=admin_principal_arn,
            aws_profile=aws_profile,
            name=name,
        )
    except AwsAdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    workspace = operator_artifact_dir.expanduser().resolve() / "terraform-workspace"
    console.print(f"[green]AWS administrative bootstrap planned.[/green] Saved plan: {plan_path}")
    console.print(
        "Review: "
        + shlex.join(("terraform", f"-chdir={workspace}", "show", "-no-color", str(plan_path))),
        soft_wrap=True,
    )
    next_command = [
        "dander",
        "init-aws-admin-apply",
        "--aws-account-id",
        aws_account_id,
        "--region",
        region,
        "--state-bucket",
        state_bucket,
        "--state-key",
        state_key,
        "--lock-table",
        lock_table,
        "--ecr-repository",
        ecr_repository_name,
        "--admin-principal-arn",
        admin_principal_arn,
        "--name",
        name,
        "--operator-artifact-dir",
        str(operator_artifact_dir),
    ]
    if aws_profile:
        next_command.extend(("--aws-profile", aws_profile))
    console.print("Next after review: " + shlex.join(next_command), soft_wrap=True)


def init_aws_admin_apply(
    aws_account_id: str = typer.Option(..., "--aws-account-id"),
    region: str = typer.Option("us-east-1", "--region"),
    state_bucket: str = typer.Option(..., "--state-bucket"),
    state_key: str = typer.Option("dander/aws/bootstrap-admin/terraform.tfstate", "--state-key"),
    lock_table: str = typer.Option("dander-terraform-locks", "--lock-table"),
    ecr_repository_name: str = typer.Option("dander", "--ecr-repository"),
    admin_principal_arn: str = typer.Option(..., "--admin-principal-arn"),
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
    operator_artifact_dir: Path = typer.Option(..., "--operator-artifact-dir"),  # noqa: B008
    infra_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_AWS_BOOTSTRAP_ADMIN_DIR, hidden=True
    ),
) -> None:
    """Apply the reviewed AWS stage-zero plan and migrate its state into S3."""
    if not typer.confirm(
        f"Apply the reviewed AWS stage-zero plan to account {aws_account_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        bootstrap = AwsAdministrativeBootstrap(infra_dir, operator_artifact_dir)
        plan_path = bootstrap.apply_saved_plan(
            aws_account_id=aws_account_id,
            region=region,
            state_bucket=state_bucket,
            state_key=state_key,
            lock_table=lock_table,
            ecr_repository_name=ecr_repository_name,
            admin_principal_arn=admin_principal_arn,
            aws_profile=aws_profile,
            name=name,
        )
    except AwsAdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    role_arn = AwsAdministrativeBootstrap.deployment_role_arn(
        aws_account_id=aws_account_id,
        region=region,
        name=name,
    )
    console.print(f"[green]AWS administrative bootstrap applied.[/green] Saved plan: {plan_path}")
    console.print(f"Deployment role: {role_arn}")
    console.print(
        "Next: configure a short-lived AWS profile that assumes this role, then promote the "
        "accepted source-free image with dander image-promote-aws."
    )


def image_promote_aws(
    source_image: str = typer.Option(
        ..., "--source-image", help="Accepted source-free OCI image ending in @sha256 digest."
    ),
    aws_account_id: str = typer.Option(..., "--aws-account-id"),
    region: str = typer.Option("us-east-1", "--region"),
    ecr_repository_name: str = typer.Option("dander", "--ecr-repository"),
    aws_profile: str = typer.Option("", "--aws-profile"),
    tag_prefix: str = typer.Option("promoted", "--tag-prefix"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
) -> None:
    """Copy an accepted source-free OCI index into ECR without rebuilding it."""
    project_dir = config.resolve().parent
    if not typer.confirm(
        f"Copy the accepted runtime image into AWS account {aws_account_id!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        promoter = RuntimeImagePromoter(project_dir)
        image = promoter.promote(
            source_image=source_image,
            aws_account_id=aws_account_id,
            region=region,
            repository_name=ecr_repository_name,
            aws_profile=aws_profile,
            tag_prefix=tag_prefix,
        )
    except ProjectBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Promoted byte-identical runtime image:[/green] {image}")
    if promoter.artifact_record_path is not None:
        console.print(f"AWS artifact record: {promoter.artifact_record_path}")
    console.print("Next: use this immutable digest with dander init-aws-plan.")


def init_aws_plan(
    project: str = typer.Option(..., "--project", help="GCP BigQuery data-plane project id."),
    state_bucket: str = typer.Option(..., "--state-bucket", help="Existing S3 state bucket."),
    container_image: str = typer.Option(
        ..., "--container-image", help="Immutable ECR image ending in @sha256 digest."
    ),
    deployment: str = typer.Option(..., "--deployment"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    state_key: str = typer.Option("dander/aws/state/terraform.tfstate", "--state-key"),
    lock_table: str = typer.Option(..., "--lock-table", help="Existing DynamoDB lock table."),
    state_region: str | None = typer.Option(None, "--state-region"),
    aws_profile: str = typer.Option("", "--aws-profile"),
    name: str = typer.Option("dander", "--name"),
    infra_dir: Path = typer.Option(_DEFAULT_AWS_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan one manifest-selected Fargate deployment without applying it."""
    try:
        manifest = load_project_config(
            config,
            platforms_path=platforms_config,
            deployment=deployment,
        )
        if manifest.launcher_provider != "fargate":
            raise ProjectConfigError(
                f"Deployment {deployment!r} does not select launcher.provider='fargate'"
            )
        manifest.validate_references(config.resolve().parent)
        runtime = manifest.platform.runtime
        plan_path = AwsTerraformBootstrap(infra_dir).execute(
            project=project,
            deployment_name=manifest.deployment_name,
            state_bucket=state_bucket,
            state_key=state_key,
            state_region=state_region or manifest.platform.region,
            lock_table=lock_table,
            container_image=container_image,
            launcher_config=manifest.resolved_launcher_config(),
            runtime_cpu=runtime.cpu,
            runtime_memory=runtime.memory,
            runtime_timeout_seconds=runtime.timeout_seconds,
            runtime_max_retries=runtime.max_retries,
            runtime_batch_rows=runtime.batch_rows,
            require_guarded_free_tier=manifest.platform.safety.require_guarded_free_tier,
            pipelines=manifest.terraform_pipelines(),
            apply=False,
            aws_profile=aws_profile,
            name=name,
        )
    except (AwsTerraformBootstrapError, ProjectConfigError) as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]AWS deployment planned.[/green] Saved plan: {plan_path}")
    console.print(
        "Review: "
        + shlex.join(("terraform", f"-chdir={infra_dir}", "show", "-no-color", str(plan_path))),
        soft_wrap=True,
    )
    next_command = [
        "dander",
        "init-aws-apply",
        "--state-bucket",
        state_bucket,
        "--state-key",
        state_key,
        "--state-region",
        state_region or manifest.platform.region,
        "--lock-table",
        lock_table,
    ]
    if aws_profile:
        next_command.extend(("--aws-profile", aws_profile))
    console.print("Next after review: " + shlex.join(next_command), soft_wrap=True)


def init_aws_apply(
    state_bucket: str = typer.Option(..., "--state-bucket", help="Existing S3 state bucket."),
    state_key: str = typer.Option("dander/aws/state/terraform.tfstate", "--state-key"),
    state_region: str = typer.Option(..., "--state-region"),
    lock_table: str = typer.Option(..., "--lock-table", help="Existing DynamoDB lock table."),
    aws_profile: str = typer.Option("", "--aws-profile"),
    infra_dir: Path = typer.Option(_DEFAULT_AWS_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply only the previously reviewed AWS Terraform plan."""
    if not typer.confirm(
        f"Apply the reviewed AWS deployment plan using state bucket {state_bucket!r}?",
        default=False,
    ):
        raise typer.Abort()
    try:
        plan_path = AwsTerraformBootstrap(infra_dir).apply_saved_plan(
            state_bucket=state_bucket,
            state_key=state_key,
            state_region=state_region,
            lock_table=lock_table,
            aws_profile=aws_profile,
        )
    except AwsTerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]AWS deployment applied.[/green] Saved plan: {plan_path}")


def register_aws_commands(app: typer.Typer) -> None:
    """Register flat, compatibility-preserving AWS command names on the root CLI."""
    app.add_typer(aws_app, name="aws")
    app.command("init-aws-admin-plan")(init_aws_admin_plan)
    app.command("init-aws-admin-apply")(init_aws_admin_apply)
    app.command("image-promote-aws")(image_promote_aws)
    app.command("init-aws-plan")(init_aws_plan)
    app.command("init-aws-apply")(init_aws_apply)


__all__ = ["register_aws_commands"]
