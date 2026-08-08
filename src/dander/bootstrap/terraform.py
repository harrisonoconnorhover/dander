"""Safe Terraform planning and explicitly-authorized application."""

from __future__ import annotations

import re
import subprocess
from decimal import Decimal, InvalidOperation
from json import dumps
from typing import TYPE_CHECKING

from dander.deployment import ExecutionProjectionError, LauncherRuntime
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_STATE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REGION = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_BIGQUERY_LOCATION = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
_BILLING_ACCOUNT = re.compile(r"^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$")
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_SECRET_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")
_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_REF = re.compile(r"^refs/(?:heads|tags)/[A-Za-z0-9._/-]+$")
_BUDGET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RUNTIME_MEMORY = re.compile(r"^[1-9][0-9]*(?:Mi|Gi)$")
_BOOTSTRAP_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)


class TerraformBootstrapError(RuntimeError):
    """Raised when the Terraform bootstrap command cannot complete."""


class TerraformBootstrap:
    """Initialize remote state and produce or apply a saved Terraform plan."""

    def __init__(self, infra_dir: Path) -> None:
        self._infra_dir = infra_dir.resolve()

    def execute(
        self,
        *,
        project: str,
        state_bucket: str,
        state_prefix: str,
        bootstrap_service_account: str,
        apply: bool,
        launcher_provider: str = "cloud_run",
        region: str = "us-central1",
        bigquery_location: str = "US",
        runtime_cpu: int = 1,
        runtime_memory: str = "512Mi",
        runtime_timeout_seconds: int = 300,
        runtime_max_retries: int = 1,
        runtime_batch_rows: int = 10_000,
        require_guarded_free_tier: bool = True,
        enable_runtime: bool = False,
        billing_account_id: str = "",
        container_image: str = "",
        druff_container_image: str = "",
        pipelines: Mapping[str, Mapping[str, object]] | None = None,
        failure_alert_email: str = "",
        secret_ids: tuple[str, ...] = (),
        github_repository: str = "",
        github_ref: str = "refs/heads/main",
        enable_cost_guard: bool = False,
        cost_guard_budget_name: str = "dander-sbx-cap",
        cost_guard_budget_amount: str = "5.00",
        live_cost_guard: bool = False,
    ) -> Path:
        """Create a saved bootstrap plan and optionally apply that exact plan.

        Args:
            project: GCP project id supplied to Terraform.
            state_bucket: Existing GCS bucket used for remote Terraform state.
            state_prefix: Object prefix for the Dander state.
            bootstrap_service_account: Existing service account that Terraform must impersonate.
            apply: Whether to apply the saved plan after planning.
            launcher_provider: Selected launcher provider ID from the deployment profile.
            region: GCP region for regional resources.
            bigquery_location: BigQuery dataset location.
            runtime_cpu: Cloud Run CPU count shared by hosted jobs.
            runtime_memory: Cloud Run memory limit shared by hosted jobs.
            runtime_timeout_seconds: Per-task Cloud Run timeout in seconds.
            runtime_max_retries: Cloud Run task retries after the initial attempt.
            runtime_batch_rows: Maximum rows sent in one BigQuery writer request.
            require_guarded_free_tier: Whether hosted jobs must run the cost-guard preflight.
            enable_runtime: Whether to provision the scheduled Cloud Run slice.
            billing_account_id: Billing account used by the managed guard and runtime safety check.
            container_image: Immutable runtime image reference including a sha256 digest.
            druff_container_image: Optional immutable Druff UI image. Empty disables hosting.
            pipelines: Expanded additive hosted-pipeline definitions from ``dander.yaml``.
            failure_alert_email: Operator email receiving hosted-pipeline failure notifications.
            secret_ids: Secret Manager container ids to create without values.
            github_repository: Optional GitHub owner/repository allowed to deploy.
            github_ref: Exact Git branch or tag ref allowed to deploy.
            enable_cost_guard: Whether to provision the budget notification function.
            cost_guard_budget_name: Exact project budget display name.
            cost_guard_budget_amount: USD budget amount, no greater than five.
            live_cost_guard: Whether over-budget events may unlink project billing.

        Returns:
            Path to the saved plan.

        Raises:
            TerraformBootstrapError: If inputs are unsafe, Terraform is unavailable, or a command
                fails.
        """
        for label, value, pattern in (
            ("project", project, _PROJECT_ID),
            ("state bucket", state_bucket, _BUCKET_NAME),
            ("state prefix", state_prefix, _STATE_PREFIX),
            ("region", region, _REGION),
            ("BigQuery location", bigquery_location, _BIGQUERY_LOCATION),
        ):
            if not pattern.fullmatch(value):
                raise TerraformBootstrapError(f"Invalid {label}: {value!r}")
        if not _BOOTSTRAP_SERVICE_ACCOUNT.fullmatch(bootstrap_service_account):
            raise TerraformBootstrapError(
                "Platform bootstrap requires --bootstrap-service-account with a valid "
                "service-account email"
            )
        validate_runtime_settings(
            cpu=runtime_cpu,
            memory=runtime_memory,
            timeout_seconds=runtime_timeout_seconds,
            max_retries=runtime_max_retries,
            batch_rows=runtime_batch_rows,
            require_guarded_free_tier=require_guarded_free_tier,
        )

        expanded_pipelines = dict(pipelines or {})
        if enable_runtime:
            if require_guarded_free_tier and not _BILLING_ACCOUNT.fullmatch(billing_account_id):
                raise TerraformBootstrapError(
                    "Guarded runtime enablement requires --billing-account in "
                    "XXXXXX-XXXXXX-XXXXXX format"
                )
            if not _IMMUTABLE_IMAGE.fullmatch(container_image):
                raise TerraformBootstrapError(
                    "Runtime enablement requires an immutable --container-image with @sha256 digest"
                )
            if not expanded_pipelines:
                raise TerraformBootstrapError(
                    "Runtime enablement requires at least one pipeline from dander.yaml"
                )
        elif container_image:
            raise TerraformBootstrapError("--container-image requires --enable-runtime")
        elif expanded_pipelines:
            raise TerraformBootstrapError("Pipeline definitions require --enable-runtime")
        if druff_container_image:
            if not _IMMUTABLE_IMAGE.fullmatch(druff_container_image):
                raise TerraformBootstrapError(
                    "Druff hosting requires an immutable --druff-container-image "
                    "with @sha256 digest"
                )
            if not enable_runtime:
                raise TerraformBootstrapError("--druff-container-image requires --enable-runtime")
        validate_terraform_pipelines(expanded_pipelines)
        if failure_alert_email and not enable_runtime:
            raise TerraformBootstrapError("--failure-alert-email requires --enable-runtime")
        if failure_alert_email and (
            len(failure_alert_email) > 254 or not _EMAIL_ADDRESS.fullmatch(failure_alert_email)
        ):
            raise TerraformBootstrapError("Invalid failure-alert email address")
        execution_projections: dict[str, dict[str, object]] = {}
        if enable_runtime:
            try:
                launcher = build_launcher_runtime(
                    provider_id=launcher_provider,
                    region=region,
                )
                execution_projections = {
                    pipeline_id: template.as_dict()
                    for pipeline_id, template in launcher.templates.build(
                        expanded_pipelines,
                        image=container_image,
                        project=project,
                        cpu=runtime_cpu,
                        memory=runtime_memory,
                        deadline_seconds=runtime_timeout_seconds,
                        launcher_retry_count=runtime_max_retries,
                        batch_rows=runtime_batch_rows,
                        require_guarded_free_tier=require_guarded_free_tier,
                        alert_target=failure_alert_email or None,
                    ).items()
                }
            except (ExecutionProjectionError, ProviderFactoryError) as error:
                raise TerraformBootstrapError(str(error)) from error
        if billing_account_id and not (enable_runtime or enable_cost_guard):
            raise TerraformBootstrapError(
                "--billing-account requires --enable-runtime or --enable-cost-guard"
            )

        invalid_secrets = sorted(
            {secret_id for secret_id in secret_ids if not _SECRET_ID.fullmatch(secret_id)}
        )
        if invalid_secrets:
            raise TerraformBootstrapError(f"Invalid secret id: {invalid_secrets[0]!r}")
        if github_repository and not _GITHUB_REPOSITORY.fullmatch(github_repository):
            raise TerraformBootstrapError(
                f"Invalid GitHub repository: {github_repository!r}; expected owner/repository"
            )
        if github_repository and not enable_runtime:
            raise TerraformBootstrapError(
                "--github-repository requires --enable-runtime so deployment access has a target"
            )
        if not _GITHUB_REF.fullmatch(github_ref):
            raise TerraformBootstrapError(f"Invalid GitHub ref: {github_ref!r}")
        if enable_cost_guard and not _BILLING_ACCOUNT.fullmatch(billing_account_id):
            raise TerraformBootstrapError(
                "Cost-guard enablement requires --billing-account in XXXXXX-XXXXXX-XXXXXX format"
            )
        if not _BUDGET_NAME.fullmatch(cost_guard_budget_name):
            raise TerraformBootstrapError(
                "Cost-guard budget name must contain 1 to 60 safe display-name characters"
            )
        try:
            budget_amount = Decimal(cost_guard_budget_amount)
        except InvalidOperation as error:
            raise TerraformBootstrapError("Cost-guard budget amount must be a number") from error
        if not budget_amount.is_finite() or budget_amount <= 0 or budget_amount > 5:
            raise TerraformBootstrapError(
                "Cost-guard budget amount must be greater than zero and no greater than USD 5"
            )
        if live_cost_guard and not enable_cost_guard:
            raise TerraformBootstrapError("--live-cost-guard requires --enable-cost-guard")
        if enable_runtime and require_guarded_free_tier and not enable_cost_guard:
            raise TerraformBootstrapError(
                "require_guarded_free_tier=true requires --enable-cost-guard for hosted jobs"
            )

        plan_path = self._infra_dir / "dander-bootstrap.tfplan"
        self._run(
            "terraform",
            "init",
            "-reconfigure",
            f"-backend-config=bucket={state_bucket}",
            f"-backend-config=prefix={state_prefix}",
        )
        self._run(
            "terraform",
            "plan",
            f"-var=project_id={project}",
            f"-var=bootstrap_service_account={bootstrap_service_account}",
            f"-var=region={region}",
            f"-var=bigquery_location={bigquery_location}",
            f"-var=runtime_cpu={runtime_cpu}",
            f"-var=runtime_memory={runtime_memory}",
            f"-var=runtime_timeout_seconds={runtime_timeout_seconds}",
            f"-var=runtime_max_retries={runtime_max_retries}",
            f"-var=runtime_batch_rows={runtime_batch_rows}",
            f"-var=require_guarded_free_tier={str(require_guarded_free_tier).lower()}",
            f"-var=enable_scheduled_job={str(enable_runtime).lower()}",
            f"-var=billing_account_id={billing_account_id}",
            f"-var=runtime_container_image={container_image}",
            f"-var=druff_container_image={druff_container_image}",
            f"-var=pipelines={dumps(expanded_pipelines, sort_keys=True, separators=(',', ':'))}",
            "-var=execution_projections="
            f"{dumps(execution_projections, sort_keys=True, separators=(',', ':'))}",
            f"-var=failure_alert_email={failure_alert_email}",
            f"-var=secret_ids={dumps(sorted(set(secret_ids)), separators=(',', ':'))}",
            f"-var=github_repository={github_repository}",
            f"-var=github_ref={github_ref}",
            f"-var=enable_cost_guard={str(enable_cost_guard).lower()}",
            f"-var=cost_guard_budget_name={cost_guard_budget_name}",
            f"-var=cost_guard_budget_amount={budget_amount}",
            f"-var=cost_guard_simulate={str(not live_cost_guard).lower()}",
            f"-var=cost_guard_source_bucket={state_bucket if enable_cost_guard else ''}",
            f"-out={plan_path.name}",
        )
        if apply:
            self._run("terraform", "apply", plan_path.name)
        return plan_path

    def apply_saved_plan(
        self,
        *,
        project: str,
        state_bucket: str,
        state_prefix: str,
        bootstrap_service_account: str,
    ) -> Path:
        """Apply the exact plan emitted by a prior plan-only command."""
        for label, value, pattern in (
            ("project", project, _PROJECT_ID),
            ("state bucket", state_bucket, _BUCKET_NAME),
            ("state prefix", state_prefix, _STATE_PREFIX),
        ):
            if not pattern.fullmatch(value):
                raise TerraformBootstrapError(f"Invalid {label}: {value!r}")
        if not _BOOTSTRAP_SERVICE_ACCOUNT.fullmatch(bootstrap_service_account):
            raise TerraformBootstrapError(
                "Platform bootstrap requires --bootstrap-service-account with a valid "
                "service-account email"
            )
        plan_path = self._infra_dir / "dander-bootstrap.tfplan"
        if not plan_path.is_file():
            raise TerraformBootstrapError(f"Saved Terraform plan is missing: {plan_path}")
        self._run(
            "terraform",
            "init",
            "-reconfigure",
            f"-backend-config=bucket={state_bucket}",
            f"-backend-config=prefix={state_prefix}",
        )
        self._run("terraform", "apply", "-input=false", plan_path.name)
        return plan_path

    def _run(self, *args: str) -> None:
        try:
            subprocess.run(args, cwd=self._infra_dir, check=True)
        except FileNotFoundError as error:
            raise TerraformBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            command = " ".join(args[:2])
            raise TerraformBootstrapError(
                f"{command} failed with exit code {error.returncode}"
            ) from error


def build_launcher_runtime(
    *,
    provider_id: str | None = None,
    region: str | None = None,
    launcher_config: Mapping[str, object] | None = None,
) -> LauncherRuntime:
    """Build one launcher through the shared lazy provider registry."""
    raw_config = dict(launcher_config or {})
    if not raw_config:
        if provider_id is None or region is None:
            raise ProviderFactoryError("Launcher provider and region are required")
        raw_config = {"provider": provider_id, "region": region}
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        raw_config,
    )
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    if not isinstance(runtime, LauncherRuntime):
        raise ProviderFactoryError("Selected launcher provider returned an invalid runtime")
    return runtime


def validate_terraform_pipelines(pipelines: Mapping[str, Mapping[str, object]]) -> None:
    """Defensively validate the expanded project manifest before invoking Terraform."""
    required = {
        "job_name",
        "runtime_service_account_id",
        "scheduler_service_account_id",
        "source",
        "models",
        "build_models",
        "publish_dataplex",
        "schedule",
        "time_zone",
        "paused",
        "secret_env",
    }
    for pipeline_id, pipeline in pipelines.items():
        if not re.fullmatch(r"^[a-z][a-z0-9_]{1,62}$", pipeline_id):
            raise TerraformBootstrapError("Invalid pipeline id")
        if set(pipeline) != required:
            raise TerraformBootstrapError(f"Pipeline {pipeline_id!r} has an invalid shape")
        source = pipeline["source"]
        models = pipeline["models"]
        build_models = pipeline["build_models"]
        secret_env = pipeline["secret_env"]
        if not isinstance(source, str) or not _IDENTIFIER.fullmatch(source):
            raise TerraformBootstrapError(f"Pipeline {pipeline_id!r} has an invalid source")
        if (
            not isinstance(models, list)
            or any(
                not isinstance(model, str) or not _IDENTIFIER.fullmatch(model) for model in models
            )
            or not isinstance(build_models, bool)
            or (build_models and not models)
        ):
            raise TerraformBootstrapError(f"Pipeline {pipeline_id!r} has invalid models")
        if not isinstance(secret_env, dict) or any(
            not isinstance(env_name, str)
            or not _ENV_NAME.fullmatch(env_name)
            or not isinstance(secret_id, str)
            or not _SECRET_ID.fullmatch(secret_id)
            for env_name, secret_id in secret_env.items()
        ):
            raise TerraformBootstrapError(f"Pipeline {pipeline_id!r} has invalid secret bindings")


def validate_runtime_settings(
    *,
    cpu: int,
    memory: str,
    timeout_seconds: int,
    max_retries: int,
    batch_rows: int,
    require_guarded_free_tier: bool,
) -> None:
    """Reject runtime values before Terraform or any hosted workload sees them."""
    if isinstance(cpu, bool) or cpu not in {1, 2, 4, 6, 8}:
        raise TerraformBootstrapError("runtime_cpu must be one of 1, 2, 4, 6, or 8")
    if not isinstance(memory, str) or not _RUNTIME_MEMORY.fullmatch(memory):
        raise TerraformBootstrapError("runtime_memory must be a positive Mi or Gi quantity")
    for name, value, minimum, maximum in (
        ("runtime_timeout_seconds", timeout_seconds, 1, 86_400),
        ("runtime_max_retries", max_retries, 0, 10),
        ("runtime_batch_rows", batch_rows, 1, 100_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise TerraformBootstrapError(
                f"{name} must be an integer from {minimum} through {maximum}"
            )
    if not isinstance(require_guarded_free_tier, bool):
        raise TerraformBootstrapError("require_guarded_free_tier must be a boolean")
