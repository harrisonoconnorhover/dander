"""Composition and execution for the ``dander init`` command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from click import ClickException
from pydantic import ValidationError

from dander.bootstrap import (
    AdministrativeBootstrap,
    AdministrativeBootstrapError,
    ProjectBootstrapError,
    RuntimeImagePublisher,
    StateBucketBootstrap,
    TerraformBootstrap,
    TerraformBootstrapError,
    active_admin_member,
    wait_for_service_account_impersonation,
)
from dander.project import (
    PlatformRuntimeSpec,
    PlatformSafetySpec,
    PlatformSpec,
    ProjectConfigError,
    load_project_config,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console


@dataclass(frozen=True, slots=True)
class InitOptions:
    """Validated Typer inputs for one platform initialization request."""

    project: str
    state_bucket: str
    state_prefix: str
    bootstrap_service_account: str
    admin_member: str
    operator_artifact_dir: Path | None
    state_location: str
    region: str | None
    bigquery_location: str | None
    runtime_cpu: int | None
    runtime_memory: str | None
    runtime_timeout_seconds: int | None
    runtime_max_retries: int | None
    runtime_batch_rows: int | None
    require_guarded_free_tier: bool | None
    enable_runtime: bool
    billing_account_id: str
    container_image: str
    druff_container_image: str
    config: Path
    secret_ids: tuple[str, ...]
    github_repository: str
    github_ref: str
    failure_alert_email: str
    enable_cost_guard: bool | None
    cost_guard_budget_name: str
    cost_guard_budget_amount: str
    live_cost_guard: bool
    apply: bool
    infra_dir: Path


def execute_init(
    options: InitOptions,
    *,
    console: Console,
    state_bucket_bootstrap_cls: type[StateBucketBootstrap] = StateBucketBootstrap,
    administrative_bootstrap_cls: type[AdministrativeBootstrap] = AdministrativeBootstrap,
    runtime_image_publisher_cls: type[RuntimeImagePublisher] = RuntimeImagePublisher,
    terraform_bootstrap_cls: type[TerraformBootstrap] = TerraformBootstrap,
    active_admin_member_fn: Callable[..., str] = active_admin_member,
    wait_for_impersonation_fn: Callable[..., None] = wait_for_service_account_impersonation,
) -> Path:
    """Resolve, execute, and render one ``dander init`` request."""
    try:
        manifest = load_project_config(options.config)
        if options.enable_runtime:
            manifest.validate_references(options.config.resolve().parent)
        platform = resolve_platform_config(
            manifest.platform,
            region=options.region,
            bigquery_location=options.bigquery_location,
            runtime_cpu=options.runtime_cpu,
            runtime_memory=options.runtime_memory,
            runtime_timeout_seconds=options.runtime_timeout_seconds,
            runtime_max_retries=options.runtime_max_retries,
            runtime_batch_rows=options.runtime_batch_rows,
            require_guarded_free_tier=options.require_guarded_free_tier,
        )
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error

    pipelines = manifest.terraform_pipelines() if options.enable_runtime else {}
    runtime = platform.runtime
    safety = platform.safety
    enable_cost_guard = (
        safety.require_guarded_free_tier
        if options.enable_cost_guard is None
        else options.enable_cost_guard
    )
    if options.enable_runtime and safety.require_guarded_free_tier and not enable_cost_guard:
        raise ClickException(
            "platform.safety.require_guarded_free_tier=true requires the cost guard; "
            "enable it or explicitly override the safety setting"
        )
    billing_account_id = options.billing_account_id if enable_cost_guard else ""
    if not safety.require_guarded_free_tier and not enable_cost_guard:
        console.print(
            "[yellow]Warning: Dander is not managing, limiting, or preventing cloud spending "
            "for this installation.[/yellow]"
        )

    state_bucket = options.state_bucket or f"{options.project}-dander-state"
    bootstrap_account = options.bootstrap_service_account or (
        f"dander-bootstrap@{options.project}.iam.gserviceaccount.com"
    )
    confirmation = (
        f"Build/update the complete Dander platform in GCP project {options.project!r} "
        f"using state bucket {state_bucket!r}?"
    )
    if options.druff_container_image:
        confirmation = f"{confirmation[:-1]} including a public Druff interface?"
    if options.live_cost_guard:
        confirmation = f"{confirmation[:-1]} with LIVE automatic billing detachment enabled?"
    if options.apply and not typer.confirm(confirmation, default=False):
        raise typer.Abort()

    if options.apply and not options.bootstrap_service_account:
        _bootstrap_administration(
            options,
            platform=platform,
            state_bucket=state_bucket,
            bootstrap_account=bootstrap_account,
            billing_account_id=billing_account_id,
            state_bucket_bootstrap_cls=state_bucket_bootstrap_cls,
            administrative_bootstrap_cls=administrative_bootstrap_cls,
            active_admin_member_fn=active_admin_member_fn,
            wait_for_impersonation_fn=wait_for_impersonation_fn,
        )

    container_image = options.container_image
    if options.apply and options.enable_runtime and not container_image:
        try:
            container_image = runtime_image_publisher_cls(
                options.infra_dir.resolve().parent
            ).publish(
                project=options.project,
                region=platform.region,
                impersonate_service_account=bootstrap_account,
            )
        except ProjectBootstrapError as error:
            raise ClickException(str(error)) from error
    if options.enable_runtime and not container_image:
        raise typer.BadParameter(
            "plan-only runtime initialization requires an immutable image; "
            "use --apply to build and publish it automatically",
            param_hint="'--container-image'",
        )

    plan_path = execute_platform_bootstrap(
        project=options.project,
        state_bucket=state_bucket,
        state_prefix=options.state_prefix,
        bootstrap_service_account=bootstrap_account,
        apply=options.apply,
        launcher_provider=manifest.launcher_provider,
        region=platform.region,
        bigquery_location=platform.bigquery_location,
        runtime_cpu=runtime.cpu,
        runtime_memory=runtime.memory,
        runtime_timeout_seconds=runtime.timeout_seconds,
        runtime_max_retries=runtime.max_retries,
        runtime_batch_rows=runtime.batch_rows,
        require_guarded_free_tier=safety.require_guarded_free_tier,
        enable_runtime=options.enable_runtime,
        billing_account_id=billing_account_id,
        container_image=container_image,
        druff_container_image=options.druff_container_image,
        pipelines=pipelines,
        failure_alert_email=options.failure_alert_email,
        secret_ids=options.secret_ids,
        github_repository=options.github_repository,
        github_ref=options.github_ref,
        enable_cost_guard=enable_cost_guard,
        cost_guard_budget_name=options.cost_guard_budget_name,
        cost_guard_budget_amount=options.cost_guard_budget_amount,
        live_cost_guard=options.live_cost_guard,
        infra_dir=options.infra_dir,
        terraform_bootstrap_cls=terraform_bootstrap_cls,
    )

    action = "applied" if options.apply else "planned"
    console.print(f"[green]Bootstrap {action}.[/green] Saved plan: {plan_path}")
    return plan_path


def _bootstrap_administration(
    options: InitOptions,
    *,
    platform: PlatformSpec,
    state_bucket: str,
    bootstrap_account: str,
    billing_account_id: str,
    state_bucket_bootstrap_cls: type[StateBucketBootstrap],
    administrative_bootstrap_cls: type[AdministrativeBootstrap],
    active_admin_member_fn: Callable[..., str],
    wait_for_impersonation_fn: Callable[..., None],
) -> None:
    """Create stage-zero state and identity before the platform apply."""
    try:
        repository_dir = options.infra_dir.resolve().parent
        operator_dir = options.operator_artifact_dir or (
            Path("~/.dander").expanduser() / options.project / "bootstrap"
        )
        state_bucket_bootstrap_cls(cwd=repository_dir).ensure(
            project=options.project,
            bucket=state_bucket,
            location=options.state_location,
            apply=True,
        )
        admin_member = options.admin_member or active_admin_member_fn(cwd=repository_dir)
        administrative_bootstrap_cls(
            options.infra_dir / "bootstrap-admin",
            operator_dir,
        ).execute(
            project=options.project,
            state_bucket=state_bucket,
            admin_member=admin_member,
            apply=True,
            region=platform.region,
            state_location=options.state_location,
            billing_account_id=billing_account_id,
            github_repository=options.github_repository,
            github_ref=options.github_ref,
            adopt_state_bucket=True,
        )
        wait_for_impersonation_fn(
            service_account=bootstrap_account,
            project=options.project,
            cwd=repository_dir,
        )
    except (AdministrativeBootstrapError, ProjectBootstrapError) as error:
        raise ClickException(str(error)) from error


def resolve_platform_config(
    authored: PlatformSpec,
    *,
    region: str | None,
    bigquery_location: str | None,
    runtime_cpu: int | None,
    runtime_memory: str | None,
    runtime_timeout_seconds: int | None,
    runtime_max_retries: int | None,
    runtime_batch_rows: int | None,
    require_guarded_free_tier: bool | None,
) -> PlatformSpec:
    """Apply only explicit CLI overrides and revalidate the complete platform contract."""
    try:
        runtime = PlatformRuntimeSpec(
            cpu=runtime_cpu if runtime_cpu is not None else authored.runtime.cpu,
            memory=runtime_memory if runtime_memory is not None else authored.runtime.memory,
            timeout_seconds=(
                runtime_timeout_seconds
                if runtime_timeout_seconds is not None
                else authored.runtime.timeout_seconds
            ),
            max_retries=(
                runtime_max_retries
                if runtime_max_retries is not None
                else authored.runtime.max_retries
            ),
            batch_rows=(
                runtime_batch_rows
                if runtime_batch_rows is not None
                else authored.runtime.batch_rows
            ),
        )
        safety = PlatformSafetySpec(
            require_guarded_free_tier=(
                require_guarded_free_tier
                if require_guarded_free_tier is not None
                else authored.safety.require_guarded_free_tier
            )
        )
        return PlatformSpec(
            region=region if region is not None else authored.region,
            bigquery_location=(
                bigquery_location if bigquery_location is not None else authored.bigquery_location
            ),
            runtime=runtime,
            safety=safety,
        )
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in issue["loc"]) for issue in error.errors()})
        raise ProjectConfigError(
            f"Invalid explicit platform override; check: {', '.join(fields)}"
        ) from error


def execute_platform_bootstrap(
    *,
    project: str,
    state_bucket: str,
    state_prefix: str,
    bootstrap_service_account: str,
    apply: bool,
    launcher_provider: str,
    region: str,
    bigquery_location: str,
    runtime_cpu: int,
    runtime_memory: str,
    runtime_timeout_seconds: int,
    runtime_max_retries: int,
    runtime_batch_rows: int,
    require_guarded_free_tier: bool,
    enable_runtime: bool,
    billing_account_id: str,
    container_image: str,
    druff_container_image: str,
    pipelines: dict[str, dict[str, object]],
    failure_alert_email: str,
    secret_ids: tuple[str, ...],
    github_repository: str,
    github_ref: str,
    enable_cost_guard: bool,
    cost_guard_budget_name: str,
    cost_guard_budget_amount: str,
    live_cost_guard: bool,
    infra_dir: Path,
    terraform_bootstrap_cls: type[TerraformBootstrap] = TerraformBootstrap,
) -> Path:
    """Run the platform Terraform bootstrap and normalize its CLI error."""
    try:
        return terraform_bootstrap_cls(infra_dir).execute(
            project=project,
            state_bucket=state_bucket,
            state_prefix=state_prefix,
            bootstrap_service_account=bootstrap_service_account,
            apply=apply,
            launcher_provider=launcher_provider,
            region=region,
            bigquery_location=bigquery_location,
            runtime_cpu=runtime_cpu,
            runtime_memory=runtime_memory,
            runtime_timeout_seconds=runtime_timeout_seconds,
            runtime_max_retries=runtime_max_retries,
            runtime_batch_rows=runtime_batch_rows,
            require_guarded_free_tier=require_guarded_free_tier,
            enable_runtime=enable_runtime,
            billing_account_id=billing_account_id,
            container_image=container_image,
            druff_container_image=druff_container_image,
            pipelines=pipelines,
            failure_alert_email=failure_alert_email,
            secret_ids=secret_ids,
            github_repository=github_repository,
            github_ref=github_ref,
            enable_cost_guard=enable_cost_guard,
            cost_guard_budget_name=cost_guard_budget_name,
            cost_guard_budget_amount=cost_guard_budget_amount,
            live_cost_guard=live_cost_guard,
        )
    except TerraformBootstrapError as error:
        raise ClickException(str(error)) from error
