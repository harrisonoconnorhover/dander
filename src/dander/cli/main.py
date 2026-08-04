"""Dander command-line entrypoint."""

from __future__ import annotations

import os
import re
import shutil as shutil
import subprocess as subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from click import ClickException
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from dander import __version__
from dander.bootstrap import (
    AdministrativeBootstrap,
    AdministrativeBootstrapError,
    DeploymentSummary,
    DeploymentVerifier,
    ProjectBootstrapError,
    RuntimeImagePublisher,
    StateBucketBootstrap,
    TerraformBootstrap,
    TerraformBootstrapError,
    active_admin_member,
    wait_for_service_account_impersonation,
    write_summary,
)
from dander.catalog import (
    BigQueryMetadataStore,
    CatalogPublishError,
    DataplexCatalogPublisher,
    MetadataSnapshot,
    MetadataSpine,
    MetadataStore,
    SemanticRegistryError,
    SemanticRegistryPublisher,
    SqliteMetadataStore,
)
from dander.core.config import Settings
from dander.evidence import EvidenceBundle, EvidenceManifest, ProofEvidence, ProofStatus
from dander.executor import PipelineExecutor
from dander.ingestion import (
    Endpoint,
    Source,
    SourceConfig,
    load_source_config,
)
from dander.pipeline.graph_deployment import (
    GraphDeploymentPreviewer,
    GraphDeploymentSettings,
)
from dander.pipeline.graph_operations import (
    GraphOperationBinding,
    GraphOperationError,
    GraphOperations,
)
from dander.pipeline.graph_service import GraphDocumentError, serve_graph_file
from dander.pipeline.runtime import (
    BigQueryGraphRunner,
    GraphExecutionPlan,
    GraphRuntimeError,
    load_graph_for_execution,
    plan_graph_execution,
)
from dander.plugins import (
    ConnectorPluginError,
    ConnectorPluginRegistry,
    load_connector_plugins,
)
from dander.project import (
    PlatformRuntimeSpec,
    PlatformSafetySpec,
    PlatformSpec,
    ProjectConfigError,
    ProjectScaffoldError,
    load_project_config,
    scaffold_project,
)
from dander.runtime import PipelineRunner
from dander.sandbox import GuardedFreeTierVerifier, SandboxDataset, SandboxSafetyError
from dander.security import (
    ApiKeyBasic,
    ApiKeyBearer,
    AuthStrategy,
    ClientCredentialPlacement,
    DefaultSecretStore,
    EnvironmentSecretStore,
    NoAuth,
    OAuth1TBA,
    OAuth2ClientCredentials,
    OAuth2JWT,
)
from dander.state import (
    BigQueryLeaseStore,
    BigQueryRunHistoryStore,
    BigQueryWatermarkStore,
    RunHistoryStore,
    SqliteLeaseStore,
    SqliteRunHistoryStore,
    SqliteWatermarkStore,
)
from dander.transform import (
    BigQueryTransformRunner,
    TransformProject,
    TransformProjectError,
    TransformRunError,
)
from dander.writer import BigQueryReplaceWriter, BigQueryScd1Writer, SchemaEvolution

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.plugins import InstalledConnectorPlugin

app = typer.Typer(
    help="Dander — GCP-native data platform (ingest + transform + catalog).",
    no_args_is_help=True,
)
verify_app = typer.Typer(help="Verify deployed resources with read-only checks.")
metadata_app = typer.Typer(help="Inspect the durable metadata spine and run ledger.")
graph_app = typer.Typer(help="Open validated pipeline graphs to local visual editors.")
plugins_app = typer.Typer(help="Install and inspect explicitly pinned connector plugins.")
app.add_typer(verify_app, name="verify")
app.add_typer(metadata_app, name="metadata")
app.add_typer(graph_app, name="graph")
app.add_typer(plugins_app, name="plugins")
console = Console()
_SOURCE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_DEFAULT_CONNECTORS_DIR = Path("connectors")
_DEFAULT_INFRA_DIR = Path("infra")
_DEFAULT_MODELS_DIR = Path("models")
_DEFAULT_CATALOG_PATH = Path(".dander/catalog.json")
_DEFAULT_BOOTSTRAP_ADMIN_DIR = Path("infra/bootstrap-admin")
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")


def _show_version(value: bool) -> None:
    if value:
        console.print(f"dander {__version__}")
        raise typer.Exit()


@app.callback()
def cli(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show the installed Dander version and exit.",
    ),
) -> None:
    """Run the Dander command-line interface."""


@app.command("new")
def new_project(directory: Path = typer.Argument(...)) -> None:  # noqa: B008
    """Create a complete starter project without overwriting an existing path."""
    try:
        created = scaffold_project(directory)
    except ProjectScaffoldError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Created Dander project at {created}.[/green]")
    console.print(f"Next: cd {created} && dander validate")


@app.command("validate")
def validate_project(
    project_config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    connectors_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_CONNECTORS_DIR, "--connectors-dir"
    ),
    models_dir: Path = typer.Option(_DEFAULT_MODELS_DIR, "--models-dir"),  # noqa: B008
) -> None:
    """Validate the project manifest and all configured connector/model references."""
    try:
        manifest = load_project_config(project_config)
        manifest.validate_references(
            project_config.resolve().parent,
            connectors_dir=connectors_dir,
            models_dir=models_dir,
        )
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    summary = f"Validated {len(manifest.pipelines)} additive pipeline(s) from {project_config}."
    console.print(f"[green]{summary}[/green]")


@plugins_app.command("install")
def install_plugins(
    project_config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
) -> None:
    """Install the manifest's exact connector-plugin package pins."""
    try:
        manifest = load_project_config(project_config)
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    requirements = [
        f"{plugin.distribution}=={plugin.version}" for _, plugin in sorted(manifest.plugins.items())
    ]
    if not requirements:
        console.print("No connector plugins are declared in dander.yaml.")
        return
    uv_executable = shutil.which("uv")
    command = (
        (uv_executable, "pip", "install", "--python", sys.executable, *requirements)
        if uv_executable is not None
        else (sys.executable, "-m", "pip", "install", *requirements)
    )
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            check=False,
        )
    except OSError as error:
        raise ClickException("Could not start the Python package installer") from error
    if completed.returncode != 0:
        raise ClickException("Connector plugin installation failed")
    try:
        load_connector_plugins(manifest.plugins)
    except ConnectorPluginError as error:
        raise ClickException(f"Installed connector plugins are incompatible: {error}") from error
    console.print(f"[green]Installed {len(requirements)} connector plugin(s).[/green]")


@graph_app.command("serve")
def serve_graph(
    graph_file: Path = typer.Option(..., "--file", help="Existing PipelineGraph YAML/JSON file."),  # noqa: B008
    origin: str = typer.Option(
        "http://localhost:3000",
        "--origin",
        help="Exact Druff browser origin allowed to read and save the graph.",
    ),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    project_config: Path = typer.Option(  # noqa: B008
        _DEFAULT_PROJECT_CONFIG,
        "--config",
        help="Dander project manifest used for plugin discovery and execution controls.",
    ),
    pipeline: str = typer.Option(
        "",
        "--pipeline",
        help="Graph pipeline to bind to its already-deployed Cloud Run job.",
    ),
    project: str = typer.Option(
        "",
        "--project",
        help="GCP project containing the already-deployed graph job.",
    ),
    enable_deployment_preview: bool = typer.Option(
        False,
        "--enable-deployment-preview",
        help="Allow an explicit source-free image build and isolated Terraform plan.",
    ),
    state_bucket: str = typer.Option("", "--state-bucket"),
    state_prefix: str = typer.Option("dander/state", "--state-prefix"),
    bootstrap_service_account: str = typer.Option("", "--bootstrap-service-account"),
    billing_account_id: str = typer.Option("", "--billing-account"),
    failure_alert_email: str | None = typer.Option(None, "--failure-alert-email"),
    secret_ids: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--secret-id",
        help="Preserve an additional Terraform-managed secret container; repeat as needed.",
    ),
    github_repository: str = typer.Option("", "--github-repository"),
    github_ref: str = typer.Option("refs/heads/main", "--github-ref"),
    enable_cost_guard: bool | None = typer.Option(
        None,
        "--enable-cost-guard/--no-cost-guard",
    ),
    cost_guard_budget_name: str = typer.Option("dander-sbx-cap", "--cost-guard-budget-name"),
    cost_guard_budget_amount: str = typer.Option("5.00", "--cost-guard-budget-amount"),
    live_cost_guard: bool = typer.Option(False, "--live-cost-guard"),
) -> None:
    """Serve one graph file to Druff, optionally bound to one already-deployed job."""
    try:
        if bool(pipeline) != bool(project):
            raise GraphOperationError("--pipeline and --project must be supplied together")
        connector_plugins: tuple[InstalledConnectorPlugin, ...] = ()
        if project_config.is_file():
            manifest = load_project_config(project_config)
            connector_plugins = load_connector_plugins(manifest.plugins).plugins
        operations = None
        if pipeline:
            binding = GraphOperationBinding.from_project(
                graph_file=graph_file,
                project_config=project_config,
                pipeline_id=pipeline,
                project=project,
            )
            deployment_previewer = None
            if enable_deployment_preview:
                if failure_alert_email is None:
                    raise GraphOperationError(
                        "--enable-deployment-preview requires an explicit "
                        "--failure-alert-email value"
                    )
                deployment_previewer = GraphDeploymentPreviewer(
                    binding,
                    GraphDeploymentSettings(
                        state_bucket=state_bucket or f"{project}-dander-state",
                        state_prefix=state_prefix,
                        bootstrap_service_account=bootstrap_service_account
                        or f"dander-bootstrap@{project}.iam.gserviceaccount.com",
                        billing_account_id=billing_account_id,
                        failure_alert_email=failure_alert_email,
                        secret_ids=tuple(secret_ids or ()),
                        github_repository=github_repository,
                        github_ref=github_ref,
                        enable_cost_guard=enable_cost_guard,
                        cost_guard_budget_name=cost_guard_budget_name,
                        cost_guard_budget_amount=cost_guard_budget_amount,
                        live_cost_guard=live_cost_guard,
                    ),
                )
            operations = GraphOperations(binding, deployment_previewer=deployment_previewer)
        elif enable_deployment_preview:
            raise GraphOperationError(
                "--enable-deployment-preview requires --pipeline and --project"
            )
        console.print(f"Serving [bold]{graph_file.resolve()}[/bold]")
        console.print(f"Druff API: http://127.0.0.1:{port}/v1/graph (origin: {origin})")
        if operations is not None:
            console.print(
                "Operations: "
                f"{operations.binding.pipeline_id} -> {operations.binding.job_name} "
                f"({operations.binding.project}/{operations.binding.region})"
            )
            if enable_deployment_preview:
                console.print(
                    "Deployment preview: enabled (pushes a source-free candidate and creates "
                    "a temporary full-platform plan; never applies it)"
                )
        console.print(
            "Press Ctrl-C to stop. YAML formatting and comments may be normalized on save."
        )
        serve_graph_file(
            graph_file,
            origin=origin,
            port=port,
            operations=operations,
            connector_plugins=connector_plugins,
        )
    except (
        ConnectorPluginError,
        GraphDocumentError,
        GraphOperationError,
        ProjectConfigError,
    ) as error:
        raise ClickException(str(error)) from error
    except OSError as error:
        raise ClickException(f"Could not start graph service: {error}") from error
    except KeyboardInterrupt:
        console.print("\nStopped graph service.")


@app.command()
def init(
    project: str = typer.Option(..., "--project", help="GCP project id."),
    state_bucket: str = typer.Option(
        "",
        "--state-bucket",
        help="GCS Terraform-state bucket; defaults to <project>-dander-state.",
    ),
    state_prefix: str = typer.Option(
        "dander/state", "--state-prefix", help="Object prefix for Terraform state."
    ),
    bootstrap_service_account: str = typer.Option(
        "",
        "--bootstrap-service-account",
        help="Existing dander-bootstrap service account used for platform impersonation.",
    ),
    admin_member: str = typer.Option(
        "",
        "--admin-member",
        help="Stage-zero user:/group:/serviceAccount: principal; inferred from gcloud when empty.",
    ),
    operator_artifact_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--operator-artifact-dir",
        help="Secured stage-zero plan directory outside the repository.",
    ),
    state_location: str = typer.Option("US", "--state-location"),
    region: str | None = typer.Option(
        None, "--region", help="Override platform.region from dander.yaml."
    ),
    bigquery_location: str | None = typer.Option(
        None,
        "--bigquery-location",
        help="Override platform.bigquery_location from dander.yaml.",
    ),
    runtime_cpu: int | None = typer.Option(
        None, "--runtime-cpu", help="Override platform.runtime.cpu from dander.yaml."
    ),
    runtime_memory: str | None = typer.Option(
        None, "--runtime-memory", help="Override platform.runtime.memory from dander.yaml."
    ),
    runtime_timeout_seconds: int | None = typer.Option(
        None,
        "--runtime-timeout-seconds",
        help="Override platform.runtime.timeout_seconds from dander.yaml.",
    ),
    runtime_max_retries: int | None = typer.Option(
        None,
        "--runtime-max-retries",
        help="Override platform.runtime.max_retries from dander.yaml.",
    ),
    runtime_batch_rows: int | None = typer.Option(
        None,
        "--runtime-batch-rows",
        help="Override platform.runtime.batch_rows from dander.yaml.",
    ),
    require_guarded_free_tier: bool | None = typer.Option(
        None,
        "--require-guarded-free-tier/--no-require-guarded-free-tier",
        help="Override platform.safety.require_guarded_free_tier from dander.yaml.",
    ),
    enable_runtime: bool = typer.Option(
        True,
        "--enable-runtime/--no-runtime",
        help="Provision project-defined Cloud Run pipelines (enabled by default).",
    ),
    billing_account_id: str = typer.Option(
        "",
        "--billing-account",
        help="Billing account required by the guarded runtime.",
    ),
    container_image: str = typer.Option(
        "",
        "--container-image",
        help="Immutable Artifact Registry image reference ending in @sha256 digest.",
    ),
    config: Path = typer.Option(  # noqa: B008
        _DEFAULT_PROJECT_CONFIG,
        "--config",
        help="Versioned Dander project manifest containing additive pipeline definitions.",
    ),
    secret_ids: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--secret-id",
        help="Create a Secret Manager container without a value. Repeat for multiple secrets.",
    ),
    github_repository: str = typer.Option(
        "",
        "--github-repository",
        help="GitHub owner/repository allowed to deploy using keyless OIDC.",
    ),
    github_ref: str = typer.Option(
        "refs/heads/main",
        "--github-ref",
        help="Exact branch or tag ref allowed to deploy.",
    ),
    failure_alert_email: str = typer.Option(
        "",
        "--failure-alert-email",
        help="Email receiving Cloud Run failure alerts; omitted from dander.yaml for privacy.",
    ),
    enable_cost_guard: bool | None = typer.Option(
        None,
        "--enable-cost-guard/--no-cost-guard",
        help=(
            "Provision the USD 5 simulation-first budget guard; defaults to "
            "platform.safety.require_guarded_free_tier."
        ),
    ),
    cost_guard_budget_name: str = typer.Option(
        "dander-sbx-cap",
        "--cost-guard-budget-name",
        help="Exact project budget display name.",
    ),
    cost_guard_budget_amount: str = typer.Option(
        "5.00",
        "--cost-guard-budget-amount",
        help="USD project budget amount; maximum 5.00.",
    ),
    live_cost_guard: bool = typer.Option(
        False,
        "--live-cost-guard",
        help="Allow the cost guard to unlink billing. Destructive; simulation is the default.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the saved Terraform plan. Without this flag, only plan.",
    ),
    infra_dir: Path = typer.Option(_DEFAULT_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Build or update Dander's complete GCP platform from ``dander.yaml``."""
    try:
        manifest = load_project_config(config)
        if enable_runtime:
            manifest.validate_references(config.resolve().parent)
        platform = _resolve_platform_config(
            manifest.platform,
            region=region,
            bigquery_location=bigquery_location,
            runtime_cpu=runtime_cpu,
            runtime_memory=runtime_memory,
            runtime_timeout_seconds=runtime_timeout_seconds,
            runtime_max_retries=runtime_max_retries,
            runtime_batch_rows=runtime_batch_rows,
            require_guarded_free_tier=require_guarded_free_tier,
        )
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    pipelines = manifest.terraform_pipelines() if enable_runtime else {}
    runtime = platform.runtime
    safety = platform.safety
    resolved_enable_cost_guard = (
        safety.require_guarded_free_tier if enable_cost_guard is None else enable_cost_guard
    )
    if enable_runtime and safety.require_guarded_free_tier and not resolved_enable_cost_guard:
        raise ClickException(
            "platform.safety.require_guarded_free_tier=true requires the cost guard; "
            "enable it or explicitly override the safety setting"
        )
    resolved_billing_account_id = billing_account_id if resolved_enable_cost_guard else ""
    if not safety.require_guarded_free_tier and not resolved_enable_cost_guard:
        console.print(
            "[yellow]Warning: Dander is not managing, limiting, or preventing cloud spending "
            "for this installation.[/yellow]"
        )
    resolved_state_bucket = state_bucket or f"{project}-dander-state"
    resolved_bootstrap_account = bootstrap_service_account or (
        f"dander-bootstrap@{project}.iam.gserviceaccount.com"
    )
    confirmation = (
        f"Build/update the complete Dander platform in GCP project {project!r} "
        f"using state bucket {resolved_state_bucket!r}?"
    )
    if live_cost_guard:
        confirmation = f"{confirmation[:-1]} with LIVE automatic billing detachment enabled?"
    if apply and not typer.confirm(
        confirmation,
        default=False,
    ):
        raise typer.Abort()
    if apply and not bootstrap_service_account:
        try:
            repository_dir = infra_dir.resolve().parent
            resolved_operator_dir = operator_artifact_dir or (
                Path("~/.dander").expanduser() / project / "bootstrap"
            )
            StateBucketBootstrap(cwd=repository_dir).ensure(
                project=project,
                bucket=resolved_state_bucket,
                location=state_location,
                apply=True,
            )
            resolved_admin_member = admin_member or active_admin_member(cwd=repository_dir)
            AdministrativeBootstrap(
                infra_dir / "bootstrap-admin",
                resolved_operator_dir,
            ).execute(
                project=project,
                state_bucket=resolved_state_bucket,
                admin_member=resolved_admin_member,
                apply=True,
                region=platform.region,
                state_location=state_location,
                billing_account_id=resolved_billing_account_id,
                github_repository=github_repository,
                github_ref=github_ref,
                adopt_state_bucket=True,
            )
            wait_for_service_account_impersonation(
                service_account=resolved_bootstrap_account,
                project=project,
                cwd=repository_dir,
            )
        except (AdministrativeBootstrapError, ProjectBootstrapError) as error:
            raise ClickException(str(error)) from error
    if apply and enable_runtime and not container_image:
        try:
            container_image = RuntimeImagePublisher(infra_dir.resolve().parent).publish(
                project=project,
                region=platform.region,
            )
        except ProjectBootstrapError as error:
            raise ClickException(str(error)) from error
    if enable_runtime and not container_image:
        raise typer.BadParameter(
            "plan-only runtime initialization requires an immutable image; "
            "use --apply to build and publish it automatically",
            param_hint="'--container-image'",
        )
    plan_path = _execute_platform_bootstrap(
        project=project,
        state_bucket=resolved_state_bucket,
        state_prefix=state_prefix,
        bootstrap_service_account=resolved_bootstrap_account,
        apply=apply,
        region=platform.region,
        bigquery_location=platform.bigquery_location,
        runtime_cpu=runtime.cpu,
        runtime_memory=runtime.memory,
        runtime_timeout_seconds=runtime.timeout_seconds,
        runtime_max_retries=runtime.max_retries,
        runtime_batch_rows=runtime.batch_rows,
        require_guarded_free_tier=safety.require_guarded_free_tier,
        enable_runtime=enable_runtime,
        billing_account_id=resolved_billing_account_id,
        container_image=container_image,
        pipelines=pipelines,
        failure_alert_email=failure_alert_email,
        secret_ids=tuple(secret_ids or ()),
        github_repository=github_repository,
        github_ref=github_ref,
        enable_cost_guard=resolved_enable_cost_guard,
        cost_guard_budget_name=cost_guard_budget_name,
        cost_guard_budget_amount=cost_guard_budget_amount,
        live_cost_guard=live_cost_guard,
        infra_dir=infra_dir,
    )

    action = "applied" if apply else "planned"
    console.print(f"[green]Bootstrap {action}.[/green] Saved plan: {plan_path}")


def _resolve_platform_config(
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


def _execute_platform_bootstrap(
    *,
    project: str,
    state_bucket: str,
    state_prefix: str,
    bootstrap_service_account: str,
    apply: bool,
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
) -> Path:
    try:
        return TerraformBootstrap(infra_dir).execute(
            project=project,
            state_bucket=state_bucket,
            state_prefix=state_prefix,
            bootstrap_service_account=bootstrap_service_account,
            apply=apply,
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
            pipelines=pipelines,
            failure_alert_email=failure_alert_email,
            secret_ids=tuple(secret_ids or ()),
            github_repository=github_repository,
            github_ref=github_ref,
            enable_cost_guard=enable_cost_guard,
            cost_guard_budget_name=cost_guard_budget_name,
            cost_guard_budget_amount=cost_guard_budget_amount,
            live_cost_guard=live_cost_guard,
        )
    except TerraformBootstrapError as error:
        raise ClickException(str(error)) from error


@app.command("init-admin-plan")
def init_admin_plan(
    project: str = typer.Option(..., "--project", help="GCP project id."),
    state_bucket: str = typer.Option(..., "--state-bucket", help="New remote-state bucket name."),
    admin_member: str = typer.Option(
        ..., "--admin-member", help="Approved user:, serviceAccount:, or group: principal."
    ),
    region: str = typer.Option("us-central1", "--region"),
    state_location: str = typer.Option("US", "--state-location"),
    bootstrap_service_account_id: str = typer.Option(
        "dander-bootstrap", "--bootstrap-service-account-id"
    ),
    billing_account_id: str = typer.Option("", "--billing-account"),
    github_repository: str = typer.Option("", "--github-repository"),
    github_ref: str = typer.Option("refs/heads/main", "--github-ref"),
    operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ...,
        "--operator-artifact-dir",
        help="Secured directory outside the repository for plans and Terraform metadata.",
    ),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_BOOTSTRAP_ADMIN_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan stage-zero state bucket and bootstrap identity resources."""
    try:
        plan_path = AdministrativeBootstrap(infra_dir, operator_artifact_dir).execute(
            project=project,
            state_bucket=state_bucket,
            admin_member=admin_member,
            apply=False,
            region=region,
            state_location=state_location,
            bootstrap_service_account_id=bootstrap_service_account_id,
            billing_account_id=billing_account_id,
            github_repository=github_repository,
            github_ref=github_ref,
        )
    except AdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Administrative bootstrap planned.[/green] Saved plan: {plan_path}")


@app.command("init-admin-apply")
def init_admin_apply(
    project: str = typer.Option(..., "--project", help="GCP project id."),
    state_bucket: str = typer.Option(..., "--state-bucket", help="New remote-state bucket name."),
    admin_member: str = typer.Option(
        ..., "--admin-member", help="Approved user:, serviceAccount:, or group: principal."
    ),
    region: str = typer.Option("us-central1", "--region"),
    state_location: str = typer.Option("US", "--state-location"),
    bootstrap_service_account_id: str = typer.Option(
        "dander-bootstrap", "--bootstrap-service-account-id"
    ),
    billing_account_id: str = typer.Option("", "--billing-account"),
    github_repository: str = typer.Option("", "--github-repository"),
    github_ref: str = typer.Option("refs/heads/main", "--github-ref"),
    operator_artifact_dir: Path = typer.Option(  # noqa: B008
        ...,
        "--operator-artifact-dir",
        help="Secured directory outside the repository for plans and Terraform metadata.",
    ),  # noqa: B008
    infra_dir: Path = typer.Option(_DEFAULT_BOOTSTRAP_ADMIN_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply the reviewed stage-zero plan after explicit confirmation."""
    if not typer.confirm(
        f"Apply administrative bootstrap to GCP project {project!r}?", default=False
    ):
        raise typer.Abort()
    try:
        plan_path = AdministrativeBootstrap(infra_dir, operator_artifact_dir).execute(
            project=project,
            state_bucket=state_bucket,
            admin_member=admin_member,
            apply=True,
            region=region,
            state_location=state_location,
            bootstrap_service_account_id=bootstrap_service_account_id,
            billing_account_id=billing_account_id,
            github_repository=github_repository,
            github_ref=github_ref,
        )
    except AdministrativeBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Administrative bootstrap applied.[/green] Saved plan: {plan_path}")


@app.command("init-platform-plan")
def init_platform_plan(
    project: str = typer.Option(..., "--project", help="GCP project id."),
    state_bucket: str = typer.Option(..., "--state-bucket", help="Existing remote-state bucket."),
    bootstrap_service_account: str = typer.Option(..., "--bootstrap-service-account"),
    state_prefix: str = typer.Option("dander/state", "--state-prefix"),
    region: str = typer.Option("us-central1", "--region"),
    bigquery_location: str = typer.Option("US", "--bigquery-location"),
    infra_dir: Path = typer.Option(_DEFAULT_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Plan platform Terraform while impersonating the stage-zero identity."""
    plan_path = _execute_platform_bootstrap(
        project=project,
        state_bucket=state_bucket,
        state_prefix=state_prefix,
        bootstrap_service_account=bootstrap_service_account,
        apply=False,
        region=region,
        bigquery_location=bigquery_location,
        runtime_cpu=1,
        runtime_memory="512Mi",
        runtime_timeout_seconds=300,
        runtime_max_retries=1,
        runtime_batch_rows=10_000,
        require_guarded_free_tier=False,
        enable_runtime=False,
        billing_account_id="",
        container_image="",
        pipelines={},
        failure_alert_email="",
        secret_ids=(),
        github_repository="",
        github_ref="refs/heads/main",
        enable_cost_guard=False,
        cost_guard_budget_name="dander-sbx-cap",
        cost_guard_budget_amount="5.00",
        live_cost_guard=False,
        infra_dir=infra_dir,
    )
    console.print(f"[green]Platform bootstrap planned.[/green] Saved plan: {plan_path}")


@app.command("init-platform-apply")
def init_platform_apply(
    project: str = typer.Option(..., "--project", help="GCP project id."),
    state_bucket: str = typer.Option(..., "--state-bucket", help="Existing remote-state bucket."),
    bootstrap_service_account: str = typer.Option(..., "--bootstrap-service-account"),
    state_prefix: str = typer.Option("dander/state", "--state-prefix"),
    region: str = typer.Option("us-central1", "--region"),
    bigquery_location: str = typer.Option("US", "--bigquery-location"),
    infra_dir: Path = typer.Option(_DEFAULT_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Apply the reviewed platform plan through the bootstrap identity."""
    if not typer.confirm(f"Apply platform bootstrap to GCP project {project!r}?", default=False):
        raise typer.Abort()
    try:
        plan_path = TerraformBootstrap(infra_dir).apply_saved_plan(
            project=project,
            state_bucket=state_bucket,
            state_prefix=state_prefix,
            bootstrap_service_account=bootstrap_service_account,
        )
    except TerraformBootstrapError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Platform bootstrap applied.[/green] Saved plan: {plan_path}")


@verify_app.command("deployment")
def verify_deployment(
    project: str = typer.Option(..., "--project", help="GCP project id to inspect."),
    json_output: Path = typer.Option(  # noqa: B008
        Path("evidence/bootstrap-summary.json"),
        "--json",
        help="Write the sanitized verification summary to this path.",
    ),  # noqa: B008
    evidence_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--evidence-dir",
        help="Also write the complete sanitized evidence bundle to this directory.",
    ),
    state_bucket: str = typer.Option(
        ...,
        "--state-bucket",
        help="Expected remote-state bucket initialized by stage zero.",
    ),
    state_prefix: str = typer.Option(
        ...,
        "--state-prefix",
        help="Expected remote-state prefix initialized by stage zero.",
    ),
    dataset: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--dataset",
        help=(
            "Dataset to verify; repeat for multiple datasets "
            "(defaults to raw, staging, marts, dander_meta)."
        ),
    ),
    runtime_job: str | None = typer.Option(
        None,
        "--runtime-job",
        help="Cloud Run Job name to verify, for example dander-greenhouse-public.",
    ),
    scheduler_job: str | None = typer.Option(
        None,
        "--scheduler-job",
        help="Cloud Scheduler job name to verify.",
    ),
    runtime_service_account: str | None = typer.Option(
        None,
        "--runtime-service-account",
        help=(
            "Expected runtime service account email; inferred from the Cloud Run Job when omitted."
        ),
    ),
    runtime_image: str | None = typer.Option(
        None,
        "--runtime-image",
        help="Expected immutable runtime image digest.",
    ),
    secret_id: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--secret-id",
        help="Secret Manager container to verify; repeat for multiple secrets.",
    ),
    region: str = typer.Option("us-central1", "--region", help="Cloud Run/Scheduler region."),
    expect_cost_guard: bool = typer.Option(
        False,
        "--expect-cost-guard",
        help="Also verify the named budget, notification topic, function, and billing linkage.",
    ),
    billing_account_id: str | None = typer.Option(
        None,
        "--billing-account",
        help=(
            "Billing account used to verify runtime billing.viewer access and, with "
            "--expect-cost-guard, the budget guard."
        ),
    ),
    cost_guard_budget_name: str = typer.Option("dander-sbx-cap", "--cost-guard-budget-name"),
    cost_guard_amount: float = typer.Option(5.0, "--cost-guard-amount"),
    cost_guard_topic: str = typer.Option("dander-stop-billing", "--cost-guard-topic"),
    cost_guard_function: str = typer.Option("dander-stop-billing", "--cost-guard-function"),
    cost_guard_simulate: bool = typer.Option(
        True,
        "--cost-guard-simulate/--cost-guard-live",
        help="Expect simulation mode for the cost guard (the safe default).",
    ),
    publish_dataplex: bool = typer.Option(
        False,
        "--publish-dataplex",
        help="Expect the runtime to have the narrow Dataplex catalog role.",
    ),
    infra_dir: Path = typer.Option(_DEFAULT_INFRA_DIR, hidden=True),  # noqa: B008
) -> None:
    """Verify the bootstrap's actual resources and save sanitized evidence."""
    summary = DeploymentVerifier(project=project, infra_dir=infra_dir).verify(
        datasets=tuple(dataset or ("raw", "staging", "marts", "dander_meta")),
        state_bucket=state_bucket,
        state_prefix=state_prefix,
        runtime_job=runtime_job,
        scheduler_job=scheduler_job,
        runtime_service_account=runtime_service_account,
        runtime_image=runtime_image,
        secret_ids=tuple(secret_id or ()),
        region=region,
        expect_cost_guard=expect_cost_guard,
        billing_account_id=billing_account_id,
        cost_guard_budget_name=cost_guard_budget_name,
        cost_guard_amount=cost_guard_amount,
        cost_guard_topic=cost_guard_topic,
        cost_guard_function=cost_guard_function,
        cost_guard_simulate=cost_guard_simulate,
        publish_dataplex=publish_dataplex,
    )
    write_summary(summary, json_output)
    if evidence_dir is not None:
        _write_bootstrap_evidence(summary, evidence_dir)
    table = Table(title=f"Dander deployment verification: {project}")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")
    for check in summary.checks:
        table.add_row(check.name, "PASS" if check.ok else "FAIL", check.detail)
    console.print(table)
    console.print(f"Evidence: {json_output}")
    if not summary.passed:
        raise ClickException("Deployment verification failed; inspect the evidence summary.")


def _write_bootstrap_evidence(summary: DeploymentSummary, evidence_dir: Path) -> None:
    """Adapt deployment checks into the standard evidence bundle without copying payloads."""
    checks = summary.checks
    passed = summary.passed
    proof = ProofEvidence(
        status=ProofStatus.PASSED if passed else ProofStatus.FAILED,
        started_at_utc=os.environ.get("DANDER_PROOF_STARTED_AT_UTC", summary.checked_at_utc),
        ended_at_utc=summary.checked_at_utc,
        operation="deployment verification",
        resource_ids=tuple(check.name for check in checks),
        row_counts={"checks": len(checks)},
        failure_reason=None if passed else "one or more deployment checks failed",
    )
    cost_checks = tuple(check for check in checks if check.name.startswith("cost_guard"))
    proofs: dict[str, ProofEvidence] = {"bootstrap": proof}
    if cost_checks:
        cost_passed = all(check.ok for check in cost_checks)
        proofs["cost-guard"] = ProofEvidence(
            status=ProofStatus.PASSED if cost_passed else ProofStatus.FAILED,
            started_at_utc=proof.started_at_utc,
            ended_at_utc=proof.ended_at_utc,
            operation="cost-guard resource verification",
            resource_ids=tuple(check.name for check in cost_checks),
            row_counts={"checks": len(cost_checks)},
            failure_reason=None if cost_passed else "one or more cost-guard checks failed",
        )
    manifest = EvidenceManifest(
        commit_sha=os.environ.get("GITHUB_SHA", "local"),
        workflow_run_id=os.environ.get("GITHUB_RUN_ID", "local"),
        checked_at_utc=summary.checked_at_utc,
        gcp_project_alias=os.environ.get("DANDER_GCP_PROJECT_ALIAS", summary.project_id),
        container_digest=os.environ.get("DANDER_CONTAINER_DIGEST", "unknown"),
        terraform_plan_sha256=os.environ.get("DANDER_TERRAFORM_PLAN_SHA256", "unknown"),
        proofs=proofs,
    )
    EvidenceBundle(evidence_dir).write(manifest)


@app.command()
def run(
    pipeline_or_source: str = typer.Argument(
        ..., help="Pipeline name from dander.yaml (or a legacy source name from connectors/)."
    ),
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str | None = typer.Option(None, "--dataset", help="Override BQ_DATASET_RAW."),
    connectors_dir: Path = typer.Option(  # noqa: B008
        _DEFAULT_CONNECTORS_DIR, "--connectors-dir"
    ),
    project_config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate config and print the execution plan without credentials or network calls.",
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Require billing disabled and use no-DML, full-refresh sandbox storage.",
    ),
    guarded_free_tier: bool = typer.Option(
        False,
        "--guarded-free-tier",
        help="Require billing plus a <=$5 budget guard before using the production GCP path.",
    ),
    batch_rows: int = typer.Option(
        10_000,
        "--batch-rows",
        min=1,
        max=100_000,
        help="Maximum rows sent in one BigQuery writer request.",
    ),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
    state_path: Path = typer.Option(Path(".dander/state.db"), hidden=True),  # noqa: B008
    build_models: bool = typer.Option(
        False,
        "--build-models",
        help="Build all transform models and tests after successful ingestion.",
    ),
    models_dir: Path = typer.Option(_DEFAULT_MODELS_DIR, "--models-dir"),  # noqa: B008
    selected_models: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--select-model",
        help="Build/catalog one model root and its dependencies. Repeat for multiple roots.",
    ),
    catalog_output: Path | None = typer.Option(  # noqa: B008
        None,
        "--catalog-output",
        help="Write the semantic registry after transforms complete.",
    ),
    publish_dataplex: bool = typer.Option(
        False,
        "--publish-dataplex",
        help="Publish generated aspects after transforms; may incur metadata storage charges.",
    ),
    dataplex_location: str = typer.Option("us", "--dataplex-location"),
) -> None:
    """Run ingestion, then optionally build transforms and publish the metadata spine."""
    source = pipeline_or_source
    pipeline_id = pipeline_or_source
    project_pipeline = False
    graph_file: Path | None = None
    plugin_registry: ConnectorPluginRegistry | None = None
    if project_config.is_file():
        try:
            manifest = load_project_config(project_config)
            plugin_registry = load_connector_plugins(manifest.plugins)
            pipeline = manifest.pipelines.get(pipeline_or_source)
            if pipeline is not None:
                project_pipeline = True
                manifest.validate_references(
                    project_config.resolve().parent,
                    connectors_dir=connectors_dir,
                    models_dir=models_dir,
                    plugin_registry=plugin_registry,
                )
                source = pipeline.source
                if pipeline.graph is not None:
                    graph_file = project_config.resolve().parent / pipeline.graph
                elif selected_models is None:
                    selected_models = list(pipeline.models)
                build_models = build_models or pipeline.build_models
                publish_dataplex = publish_dataplex or pipeline.publish_dataplex
        except (ConnectorPluginError, ProjectConfigError) as error:
            raise ClickException(str(error)) from error
    if not _SOURCE_NAME.fullmatch(source):
        raise typer.BadParameter("Source names may contain only letters, numbers, '_' and '-'")

    config = load_source_config(connectors_dir / f"{source}.yaml")
    if config.name != source:
        raise ClickException(f"Connector file declares source {config.name!r}, expected {source!r}")
    try:
        plugin_registry = plugin_registry or load_connector_plugins({})
        plugin_registry.require_engine(config.engine)
    except ConnectorPluginError as error:
        raise ClickException(str(error)) from error
    settings = Settings()
    resolved_project = project or settings.gcp_project_id
    resolved_dataset = dataset or settings.bq_dataset_raw
    if sandbox and guarded_free_tier:
        raise ClickException("--sandbox and --guarded-free-tier are mutually exclusive")

    graph_plan: GraphExecutionPlan | None = None
    if graph_file is not None:
        if build_models or selected_models is not None:
            raise ClickException("Graph pipelines do not accept --build-models or --select-model")
        if catalog_output is not None or publish_dataplex:
            raise ClickException("Graph metadata publication is not supported yet")
        try:
            graph = load_graph_for_execution(graph_file)
            graph_plan = plan_graph_execution(
                graph,
                config,
                project=resolved_project or "dander-dry-run",
                dataset=resolved_dataset,
            )
        except GraphRuntimeError as error:
            raise ClickException(str(error)) from error

    if dry_run:
        _print_plan(
            config.name,
            resolved_project,
            resolved_dataset,
            _selected_endpoints(config, graph_plan),
            sandbox=sandbox,
            guarded_free_tier=guarded_free_tier,
            batch_rows=batch_rows,
        )
        if graph_plan is not None:
            _print_graph_plan(graph_plan, project=resolved_project)
        return
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")

    if sandbox:
        try:
            SandboxDataset().prepare(resolved_project, resolved_dataset)
        except SandboxSafetyError as error:
            raise ClickException(str(error)) from error
    elif guarded_free_tier:
        try:
            GuardedFreeTierVerifier().require_guarded(
                resolved_project,
                budget_name=budget_name,
            )
        except SandboxSafetyError as error:
            raise ClickException(str(error)) from error

    secrets = EnvironmentSecretStore() if sandbox else DefaultSecretStore()
    auth = _build_auth(config, secrets)
    try:
        source_adapter = _build_source_adapter(config, auth, plugin_registry=plugin_registry)
    except ConnectorPluginError as error:
        raise ClickException(str(error)) from error
    control_dataset = settings.bq_dataset_metadata if project_pipeline else resolved_dataset
    history = (
        SqliteRunHistoryStore(state_path)
        if sandbox
        else BigQueryRunHistoryStore(
            project=resolved_project,
            dataset=control_dataset,
        )
    )
    leases = (
        SqliteLeaseStore(state_path)
        if sandbox
        else BigQueryLeaseStore(
            project=resolved_project,
            dataset=control_dataset,
        )
    )
    metadata_store = None
    if project_pipeline and graph_plan is None:
        metadata_store = (
            SqliteMetadataStore(state_path)
            if sandbox
            else BigQueryMetadataStore(
                project=resolved_project,
                dataset=settings.bq_dataset_metadata,
            )
        )
    dataplex_publisher = (
        DataplexCatalogPublisher(project=resolved_project, location=dataplex_location)
        if publish_dataplex
        else None
    )
    ingestion_runner = PipelineRunner(
        source=source_adapter,
        writer=(
            BigQueryReplaceWriter(project=resolved_project, max_batch_rows=batch_rows)
            if sandbox
            else BigQueryScd1Writer(
                project=resolved_project,
                max_batch_rows=batch_rows,
                schema_evolution=(
                    SchemaEvolution.ADDITIVE if project_pipeline else SchemaEvolution.STRICT
                ),
            )
        ),
        watermarks=(
            SqliteWatermarkStore(state_path)
            if sandbox
            else BigQueryWatermarkStore(
                project=resolved_project,
                dataset=resolved_dataset,
            )
        ),
        project=resolved_project,
        dataset=resolved_dataset,
        resume_from_watermark=not sandbox,
        batch_rows=batch_rows,
        endpoint_names=(graph_plan.bindings.endpoint_names if graph_plan is not None else None),
    )
    try:
        result = PipelineExecutor(
            pipeline_id=pipeline_id,
            source_config=config,
            ingestion=ingestion_runner,
            history=history,
            project=resolved_project,
            models_dir=graph_file.parent if graph_file is not None else models_dir,
            selected_models=None if graph_plan is not None else selected_models,
            build_models=graph_plan is not None or build_models,
            transform_runner=(
                BigQueryGraphRunner(plan=graph_plan, project=resolved_project)
                if graph_plan is not None
                else BigQueryTransformRunner(project=resolved_project)
                if build_models
                else None
            ),
            metadata_store=metadata_store,
            registry_output=catalog_output,
            dataplex_publisher=dataplex_publisher,
            leases=leases,
        ).execute()
    except (
        CatalogPublishError,
        SemanticRegistryError,
        TransformProjectError,
        TransformRunError,
        GraphRuntimeError,
    ) as error:
        raise ClickException(str(error)) from error

    if result.skipped:
        console.print(f"Dander run {result.run_id} skipped: pipeline already active.")
        return

    table = Table(title=f"Dander run {result.run_id}")
    table.add_column("Endpoint")
    table.add_column("Extracted", justify="right")
    table.add_column("Affected", justify="right")
    table.add_column("Cursor committed")
    for endpoint in result.ingestion.endpoints:
        table.add_row(
            endpoint.endpoint,
            str(endpoint.extracted),
            str(endpoint.affected),
            "yes" if endpoint.committed_cursor is not None else "no",
        )
    console.print(table)
    if graph_plan is not None:
        console.print(f"Graph targets: {', '.join(result.models)}")


def _run_post_ingestion(
    *,
    project: str,
    models_dir: Path,
    selected_models: Sequence[str] | None,
    build_models: bool,
    catalog_output: Path | None,
    publish_dataplex: bool,
    dataplex_location: str,
) -> None:
    """Execute the hosted transform/catalog tail only after ingestion commits."""
    try:
        if build_models:
            BigQueryTransformRunner(project=project).build(
                models_dir,
                selected=selected_models,
            )
        if catalog_output is None and not publish_dataplex:
            return
        transform_project = TransformProject.load(models_dir, project_id=project)
        assets = MetadataSpine().compile(transform_project, selected=selected_models)
        manifest = MetadataSpine().manifest(assets)
        if catalog_output is not None:
            SemanticRegistryPublisher().publish(manifest, catalog_output)
        if publish_dataplex:
            publisher = DataplexCatalogPublisher(
                project=project,
                location=dataplex_location,
            )
            for asset in assets:
                publisher.publish(asset)
    except (
        CatalogPublishError,
        SemanticRegistryError,
        TransformProjectError,
        TransformRunError,
    ) as error:
        raise ClickException(str(error)) from error


@app.command()
def build(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    selected: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--select",
        help="Build one model and its model dependencies. Repeat to select multiple models.",
    ),
    models_dir: Path = typer.Option(_DEFAULT_MODELS_DIR, "--models-dir"),  # noqa: B008
    guarded_free_tier: bool = typer.Option(
        False,
        "--guarded-free-tier",
        help="Require the <=$5 budget guard before submitting BigQuery queries.",
    ),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
) -> None:
    """Build selected SQL models in dependency order and run their data tests."""
    resolved_project = project or Settings().gcp_project_id
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")
    _require_transform_guard(
        resolved_project,
        guarded_free_tier=guarded_free_tier,
        budget_name=budget_name,
    )
    try:
        result = BigQueryTransformRunner(project=resolved_project).build(
            models_dir,
            selected=selected,
        )
    except (TransformProjectError, TransformRunError) as error:
        raise ClickException(str(error)) from error
    _print_transform_result("Built", result.models, result.assertions)


@app.command("test")
def test_models(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    selected: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--select",
        help="Test one model and its model dependencies. Repeat to select multiple models.",
    ),
    models_dir: Path = typer.Option(_DEFAULT_MODELS_DIR, "--models-dir"),  # noqa: B008
    guarded_free_tier: bool = typer.Option(
        False,
        "--guarded-free-tier",
        help="Require the <=$5 budget guard before submitting BigQuery queries.",
    ),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
) -> None:
    """Run declared generic tests against existing model relations."""
    resolved_project = project or Settings().gcp_project_id
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")
    _require_transform_guard(
        resolved_project,
        guarded_free_tier=guarded_free_tier,
        budget_name=budget_name,
    )
    try:
        result = BigQueryTransformRunner(project=resolved_project).test(
            models_dir,
            selected=selected,
        )
    except (TransformProjectError, TransformRunError) as error:
        raise ClickException(str(error)) from error
    _print_transform_result("Tested", result.models, result.assertions)


@app.command()
def catalog(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    selected: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--select",
        help="Catalog one model and its dependencies. Repeat to select multiple models.",
    ),
    models_dir: Path = typer.Option(_DEFAULT_MODELS_DIR, "--models-dir"),  # noqa: B008
    output: Path = typer.Option(_DEFAULT_CATALOG_PATH, "--output"),  # noqa: B008
    publish_dataplex: bool = typer.Option(
        False,
        "--publish-dataplex",
        help="Attach generated aspects to BigQuery catalog entries; may incur metadata storage.",
    ),
    location: str = typer.Option(
        "us",
        "--location",
        help="Dataplex location for the BigQuery system entry.",
    ),
    guarded_free_tier: bool = typer.Option(
        False,
        "--guarded-free-tier",
        help="Require the <=$5 budget guard before Dataplex publication.",
    ),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
) -> None:
    """Compile model metadata into a local registry and optionally publish Dataplex aspects."""
    resolved_project = project or Settings().gcp_project_id
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")
    try:
        transform_project = TransformProject.load(models_dir, project_id=resolved_project)
        assets = MetadataSpine().compile(transform_project, selected=selected)
        manifest = MetadataSpine().manifest(assets)
        registry_path = SemanticRegistryPublisher().publish(manifest, output)
    except (SemanticRegistryError, TransformProjectError) as error:
        raise ClickException(str(error)) from error

    published = 0
    if publish_dataplex:
        _require_transform_guard(
            resolved_project,
            guarded_free_tier=guarded_free_tier,
            budget_name=budget_name,
        )
        try:
            publisher = DataplexCatalogPublisher(
                project=resolved_project,
                location=location,
            )
            for asset in assets:
                publisher.publish(asset)
                published += 1
        except CatalogPublishError as error:
            raise ClickException(str(error)) from error

    console.print(
        f"[green]Cataloged {len(assets)} model(s) in {registry_path}; "
        f"published {published} Dataplex entr{'y' if published == 1 else 'ies'}.[/green]"
    )


@metadata_app.command("list")
def metadata_list(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str = typer.Option("dander_meta", "--dataset"),
    local: bool = typer.Option(False, "--local", help="Read the local SQLite spine."),
    state_path: Path = typer.Option(Path(".dander/state.db"), "--state-path"),  # noqa: B008
) -> None:
    """List source and model assets in the current metadata snapshots."""
    snapshots = _metadata_snapshots(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    )
    table = Table(title="Dander metadata spine")
    table.add_column("Pipeline")
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Relation")
    for snapshot in snapshots:
        source = snapshot.manifest.get("source")
        if isinstance(source, dict):
            table.add_row(snapshot.pipeline_id, "source", str(source.get("name", "")), "")
        for asset in _manifest_assets(snapshot):
            table.add_row(
                snapshot.pipeline_id,
                "model",
                str(asset.get("name", "")),
                str(asset.get("relation", "")),
            )
    console.print(table)


@metadata_app.command("show")
def metadata_show(
    name: str = typer.Argument(..., help="Source, model, or metric name."),
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str = typer.Option("dander_meta", "--dataset"),
    local: bool = typer.Option(False, "--local"),
    state_path: Path = typer.Option(Path(".dander/state.db"), "--state-path"),  # noqa: B008
) -> None:
    """Show one governed source, model, or metric definition as JSON."""
    matches: list[dict[str, object]] = []
    for snapshot in _metadata_snapshots(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    ):
        source = snapshot.manifest.get("source")
        if isinstance(source, dict) and source.get("name") == name:
            matches.append({"pipeline_id": snapshot.pipeline_id, "kind": "source", **source})
        for asset in _manifest_assets(snapshot):
            if asset.get("name") == name:
                matches.append({"pipeline_id": snapshot.pipeline_id, "kind": "model", **asset})
            metrics = asset.get("metrics")
            if isinstance(metrics, list):
                for metric in metrics:
                    if isinstance(metric, dict) and metric.get("name") == name:
                        matches.append(
                            {
                                "pipeline_id": snapshot.pipeline_id,
                                "kind": "metric",
                                "relation": asset.get("relation", ""),
                                **metric,
                            }
                        )
    if not matches:
        raise ClickException(f"Metadata entry {name!r} was not found")
    console.print_json(data=matches[0] if len(matches) == 1 else matches)


@metadata_app.command("lineage")
def metadata_lineage(
    model: str = typer.Argument(..., help="Model name."),
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str = typer.Option("dander_meta", "--dataset"),
    local: bool = typer.Option(False, "--local"),
    state_path: Path = typer.Option(Path(".dander/state.db"), "--state-path"),  # noqa: B008
) -> None:
    """Show direct upstream relations for one governed model."""
    table = Table(title=f"Dander lineage: {model}")
    table.add_column("Model relation")
    table.add_column("Upstream relation")
    found = False
    for snapshot in _metadata_snapshots(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    ):
        for asset in _manifest_assets(snapshot):
            if asset.get("name") != model:
                continue
            found = True
            upstream = asset.get("upstream_relations")
            if isinstance(upstream, list):
                for relation in upstream:
                    table.add_row(str(asset.get("relation", "")), str(relation))
    if not found:
        raise ClickException(f"Model {model!r} was not found in the metadata spine")
    console.print(table)


@metadata_app.command("metrics")
def metadata_metrics(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str = typer.Option("dander_meta", "--dataset"),
    local: bool = typer.Option(False, "--local"),
    state_path: Path = typer.Option(Path(".dander/state.db"), "--state-path"),  # noqa: B008
) -> None:
    """List governed metric names, calculations, and definitions."""
    table = Table(title="Dander governed metrics")
    table.add_column("Metric")
    table.add_column("Relation")
    table.add_column("Calculation")
    table.add_column("Definition")
    for snapshot in _metadata_snapshots(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    ):
        for asset in _manifest_assets(snapshot):
            metrics = asset.get("metrics")
            if not isinstance(metrics, list):
                continue
            for metric in metrics:
                if isinstance(metric, dict):
                    table.add_row(
                        str(metric.get("name", "")),
                        str(asset.get("relation", "")),
                        str(metric.get("calculation", "")),
                        str(metric.get("description", "")),
                    )
    console.print(table)


@metadata_app.command("runs")
def metadata_runs(
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str = typer.Option("dander_meta", "--dataset"),
    pipeline: str | None = typer.Option(None, "--pipeline"),
    limit: int = typer.Option(20, "--limit", min=1, max=1000),
    local: bool = typer.Option(False, "--local"),
    state_path: Path = typer.Option(Path(".dander/state.db"), "--state-path"),  # noqa: B008
) -> None:
    """List recent end-to-end pipeline outcomes from the durable run ledger."""
    history = _run_history_store(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    )
    table = Table(title="Dander pipeline runs")
    for column in ("Run", "Pipeline", "Status", "Stage", "Rows", "Models", "Tests", "Assets"):
        table.add_column(column)
    for record in history.recent(limit=limit, pipeline_id=pipeline):
        table.add_row(
            record.run_id,
            record.pipeline_id,
            record.status.value,
            record.stage.value,
            str(record.affected),
            str(record.models),
            str(record.assertions),
            str(record.assets),
        )
    console.print(table)


def _metadata_snapshots(
    *,
    project: str | None,
    dataset: str,
    local: bool,
    state_path: Path,
) -> tuple[MetadataSnapshot, ...]:
    return _metadata_store(
        project=project,
        dataset=dataset,
        local=local,
        state_path=state_path,
    ).snapshots()


def _metadata_store(
    *,
    project: str | None,
    dataset: str,
    local: bool,
    state_path: Path,
) -> MetadataStore:
    if local:
        return SqliteMetadataStore(state_path)
    resolved_project = project or Settings().gcp_project_id
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")
    return BigQueryMetadataStore(project=resolved_project, dataset=dataset)


def _run_history_store(
    *,
    project: str | None,
    dataset: str,
    local: bool,
    state_path: Path,
) -> RunHistoryStore:
    if local:
        return SqliteRunHistoryStore(state_path)
    resolved_project = project or Settings().gcp_project_id
    if not resolved_project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")
    return BigQueryRunHistoryStore(project=resolved_project, dataset=dataset)


def _manifest_assets(snapshot: MetadataSnapshot) -> tuple[dict[str, object], ...]:
    raw_assets = snapshot.manifest.get("assets")
    if not isinstance(raw_assets, list):
        return ()
    return tuple(asset for asset in raw_assets if isinstance(asset, dict))


def _require_transform_guard(
    project: str,
    *,
    guarded_free_tier: bool,
    budget_name: str,
) -> None:
    if not guarded_free_tier:
        return
    try:
        GuardedFreeTierVerifier().require_guarded(project, budget_name=budget_name)
    except SandboxSafetyError as error:
        raise ClickException(str(error)) from error


def _print_transform_result(action: str, models: Sequence[str], assertions: int) -> None:
    table = Table(title=f"Dander transform: {action.lower()}")
    table.add_column("Model")
    for model in models:
        table.add_row(model)
    console.print(table)
    summary = f"{action} {len(models)} model(s); {assertions} assertion(s) passed."
    console.print(f"[green]{summary}[/green]")


def _build_source_adapter(
    config: SourceConfig,
    auth: AuthStrategy,
    *,
    plugin_registry: ConnectorPluginRegistry | None = None,
) -> Source:
    """Select the extraction implementation declared by the connector."""
    registry = plugin_registry or load_connector_plugins({})
    return registry.build_source(config, auth)


def _build_auth(
    config: SourceConfig,
    secrets: DefaultSecretStore | EnvironmentSecretStore,
) -> AuthStrategy:
    """Construct a supported authentication strategy from validated connector metadata."""
    if config.auth_strategy == "none":
        return NoAuth()
    if config.auth_strategy == "api_key_basic":
        if config.auth_ref is None:
            raise ClickException("api_key_basic connector is missing auth_ref")
        return ApiKeyBasic(secrets, config.auth_ref)
    if config.auth_strategy == "api_key_bearer":
        if config.auth_ref is None:
            raise ClickException("api_key_bearer connector is missing auth_ref")
        return ApiKeyBearer(secrets, config.auth_ref)
    if config.auth_strategy == "oauth2_client_credentials":
        token_url = config.auth_options["token_url"]
        subject = config.auth_options.get("subject")
        credential_placement = config.auth_options.get("credential_placement", "basic")
        if not isinstance(token_url, str):
            raise ClickException("OAuth token_url must be a string")
        if subject is not None and (isinstance(subject, bool) or not isinstance(subject, int)):
            raise ClickException("OAuth subject must be an integer Greenhouse user id")
        return OAuth2ClientCredentials(
            secrets,
            client_id_ref=config.auth_refs["client_id"],
            client_secret_ref=config.auth_refs["client_secret"],
            token_url=token_url,
            subject=subject,
            credential_placement=ClientCredentialPlacement(str(credential_placement)),
        )
    if config.auth_strategy == "oauth2_jwt":
        audience = config.auth_options.get("audience")
        if audience is not None and not isinstance(audience, str):
            raise ClickException("OAuth JWT audience must be a string")
        subject = config.auth_options.get("subject")
        if subject is not None and not isinstance(subject, str):
            raise ClickException("OAuth JWT subject must be a string")
        scope = config.auth_options.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ClickException("OAuth JWT scope must be a string")
        default_expires_in = config.auth_options.get("default_expires_in", 300)
        if isinstance(default_expires_in, bool) or not isinstance(default_expires_in, int):
            raise ClickException("OAuth JWT default_expires_in must be an integer")
        assertion_lifetime = config.auth_options.get("assertion_lifetime", 3600)
        if isinstance(assertion_lifetime, bool) or not isinstance(assertion_lifetime, int):
            raise ClickException("OAuth JWT assertion_lifetime must be an integer")
        return OAuth2JWT(
            secrets,
            issuer_ref=config.auth_refs["issuer"],
            private_key_ref=config.auth_refs["private_key"],
            token_url=str(config.auth_options["token_url"]),
            audience=audience,
            scope=scope,
            subject=subject,
            assertion_lifetime=assertion_lifetime,
            default_expires_in=default_expires_in,
        )
    if config.auth_strategy == "oauth1_tba":
        return OAuth1TBA(
            secrets,
            account_id=str(config.auth_options["account_id"]),
            consumer_key_ref=config.auth_refs["consumer_key"],
            consumer_secret_ref=config.auth_refs["consumer_secret"],
            token_id_ref=config.auth_refs["token_id"],
            token_secret_ref=config.auth_refs["token_secret"],
        )
    raise ClickException(f"Unsupported auth strategy: {config.auth_strategy!r}")


def _print_plan(
    source: str,
    project: str,
    dataset: str,
    endpoints: Sequence[Endpoint],
    *,
    sandbox: bool = False,
    guarded_free_tier: bool = False,
    batch_rows: int = 10_000,
) -> None:
    """Render a credential-free execution plan."""
    table = Table(title=f"Dander dry run: {source}")
    table.add_column("Endpoint")
    table.add_column("Target")
    table.add_column("Mode")
    for endpoint in endpoints:
        name = endpoint.name
        if sandbox:
            mode = "REPLACE (sandbox)"
        elif guarded_free_tier:
            mode = "SCD1 (guarded billing)"
        else:
            mode = "SCD1"
        table.add_row(str(name), f"{project or '<unset>'}.{dataset}.{source}_{name}", mode)
    console.print(table)
    console.print(f"Writer batch rows: {batch_rows}")


def _selected_endpoints(
    config: SourceConfig,
    graph_plan: GraphExecutionPlan | None,
) -> list[Endpoint]:
    if graph_plan is None:
        return list(config.endpoints)
    selected = set(graph_plan.bindings.endpoint_names)
    return [endpoint for endpoint in config.endpoints if endpoint.name in selected]


def _print_graph_plan(plan: GraphExecutionPlan, *, project: str) -> None:
    """Render the compiled target portion of a credential-free graph plan."""
    table = Table(title="PipelineGraph targets")
    table.add_column("Node")
    table.add_column("Target")
    table.add_column("Mode")
    for target in plan.targets:
        target_project = project or "<runtime-project>"
        table.add_row(
            target.node_id,
            f"{target_project}.{target.target.dataset}.{target.target.table}",
            target.write_mode.value.upper(),
        )
    console.print(table)


if __name__ == "__main__":
    app()
