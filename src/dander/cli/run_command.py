"""Composition and presentation for the ``dander run`` command."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer
from click import ClickException
from rich.table import Table

from dander.catalog import (
    CatalogPublishError,
    MetadataStore,
    SemanticRegistryError,
    SqliteMetadataStore,
)
from dander.cli.provider_runtime import build_catalog_publisher, build_secret_store
from dander.compatibility import CompatibilityError, load_runtime_compatibility
from dander.core.config import Settings
from dander.executor import PipelineExecutionResult, PipelineExecutor
from dander.ingestion import Endpoint, Source, SourceConfig, load_source_config
from dander.pipeline.runtime import (
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
from dander.project import ProjectConfigError, load_project_config
from dander.providers import (
    ProviderFactoryError,
    ProviderKind,
    default_provider_registry,
)
from dander.runtime import PipelineRunner
from dander.sandbox import GuardedFreeTierVerifier, SandboxDataset, SandboxSafetyError
from dander.security import (
    ApiKeyBasic,
    ApiKeyBearer,
    AuthStrategy,
    ClientCredentialPlacement,
    NoAuth,
    OAuth1TBA,
    OAuth2ClientCredentials,
    OAuth2JWT,
)
from dander.state import (
    LeaseStore,
    RunHistoryStore,
    SqliteLeaseStore,
    SqliteRunHistoryStore,
    SqliteWatermarkStore,
    StateRuntime,
    WatermarkStore,
)
from dander.transform import TransformProjectError, TransformRunError
from dander.warehouse import WarehouseRuntime, WarehouseTransformRunner
from dander.writer import SchemaEvolution

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from rich.console import Console

    from dander.core.interfaces import SecretStoreProvider

_SOURCE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Validated Typer inputs for one ingestion command invocation."""

    pipeline_or_source: str
    project: str | None
    dataset: str | None
    connectors_dir: Path
    project_config: Path
    platforms_config: Path | None
    deployment: str | None
    dry_run: bool
    sandbox: bool
    guarded_free_tier: bool
    batch_rows: int
    budget_name: str
    state_path: Path
    build_models: bool
    models_dir: Path
    selected_models: Sequence[str] | None
    catalog_output: Path | None
    publish_dataplex: bool
    dataplex_location: str


@dataclass(frozen=True, slots=True)
class _ResolvedRun:
    pipeline_id: str
    source_config: SourceConfig
    plugin_registry: ConnectorPluginRegistry
    project_pipeline: bool
    graph_file: Path | None
    graph_plan: GraphExecutionPlan | None
    project: str
    dataset: str
    metadata_dataset: str
    selected_models: tuple[str, ...] | None
    build_models: bool
    publish_dataplex: bool
    warehouse_provider: str
    warehouse_config: dict[str, object]
    warehouse_catalog: str
    state_provider: str
    state_config: dict[str, object]
    catalog_provider: str
    secret_provider: str


@dataclass(frozen=True, slots=True)
class _ControlStores:
    history: RunHistoryStore
    leases: LeaseStore
    watermarks: WatermarkStore
    metadata: MetadataStore | None


def execute_run(
    options: RunOptions,
    *,
    console: Console,
    run_id: str | None = None,
    render: bool = True,
) -> PipelineExecutionResult | None:
    """Resolve, execute, and render one ``dander run`` request."""
    resolved = _resolve_run(options)
    if options.dry_run:
        _render_dry_run(options, resolved, console=console)
        return None
    if _requires_gcp_project(resolved) and not resolved.project:
        raise ClickException("GCP project is required via --project or GCP_PROJECT_ID")

    _verify_safety(options, resolved)
    executor = _build_executor(options, resolved)
    try:
        result = executor.execute() if run_id is None else executor.execute(run_id=run_id)
    except (
        CatalogPublishError,
        SemanticRegistryError,
        TransformProjectError,
        TransformRunError,
        GraphRuntimeError,
    ) as error:
        raise ClickException(str(error)) from error
    if render:
        _render_result(result, resolved.graph_plan, console=console)
    return result


def _resolve_run(options: RunOptions) -> _ResolvedRun:
    source = options.pipeline_or_source
    project_pipeline = False
    graph_file: Path | None = None
    selected_models = (
        tuple(options.selected_models) if options.selected_models is not None else None
    )
    build_models = options.build_models
    publish_dataplex = options.publish_dataplex
    warehouse_provider = "bigquery"
    warehouse_config: dict[str, object] = {"provider": "bigquery", "location": "US"}
    state_provider = "bigquery"
    state_config: dict[str, object] = {"provider": "bigquery"}
    catalog_provider = "dataplex"
    secret_provider = "gcp_secret_manager"
    plugin_registry: ConnectorPluginRegistry | None = None

    if options.project_config.is_file():
        try:
            manifest = load_project_config(
                options.project_config,
                platforms_path=options.platforms_config,
                deployment=options.deployment,
            )
            plugin_registry = load_connector_plugins(manifest.plugins)
            pipeline = manifest.pipelines.get(options.pipeline_or_source)
            if pipeline is not None:
                project_pipeline = True
                manifest.validate_references(
                    options.project_config.resolve().parent,
                    connectors_dir=options.connectors_dir,
                    models_dir=options.models_dir,
                    plugin_registry=plugin_registry,
                )
                source = pipeline.source
                if pipeline.graph is not None:
                    graph_file = options.project_config.resolve().parent / pipeline.graph
                elif selected_models is None:
                    selected_models = tuple(pipeline.models)
                build_models = build_models or pipeline.build_models
                publish_dataplex = publish_dataplex or pipeline.publish_dataplex
                warehouse_provider = manifest.warehouse_provider
                warehouse_config = manifest.warehouse_config
                state_provider = manifest.state_provider
                state_config = manifest.state_config
                catalog_provider = (
                    "dataplex" if options.publish_dataplex else manifest.catalog_provider
                )
                secret_provider = manifest.secret_provider
        except (ConnectorPluginError, ProjectConfigError) as error:
            raise ClickException(str(error)) from error

    if not _SOURCE_NAME.fullmatch(source):
        raise typer.BadParameter("Source names may contain only letters, numbers, '_' and '-'")
    config = load_source_config(options.connectors_dir / f"{source}.yaml")
    if config.name != source:
        raise ClickException(f"Connector file declares source {config.name!r}, expected {source!r}")
    try:
        plugin_registry = plugin_registry or load_connector_plugins({})
        plugin_registry.require_engine(config.engine)
    except ConnectorPluginError as error:
        raise ClickException(str(error)) from error

    settings = Settings()
    resolved_project = options.project or settings.gcp_project_id
    resolved_dataset = options.dataset or settings.bq_dataset_raw
    warehouse_catalog = (
        str(warehouse_config["database"])
        if warehouse_provider == "postgresql"
        else resolved_project
    )
    if options.sandbox and options.guarded_free_tier:
        raise ClickException("--sandbox and --guarded-free-tier are mutually exclusive")
    graph_plan = _resolve_graph_plan(
        graph_file,
        config,
        project=warehouse_catalog,
        dataset=resolved_dataset,
        build_models=build_models,
        selected_models=selected_models,
        catalog_output=options.catalog_output,
        publish_dataplex=publish_dataplex,
    )
    return _ResolvedRun(
        pipeline_id=options.pipeline_or_source,
        source_config=config,
        plugin_registry=plugin_registry,
        project_pipeline=project_pipeline,
        graph_file=graph_file,
        graph_plan=graph_plan,
        project=resolved_project,
        dataset=resolved_dataset,
        metadata_dataset=settings.bq_dataset_metadata,
        selected_models=selected_models,
        build_models=build_models,
        publish_dataplex=publish_dataplex,
        warehouse_provider=warehouse_provider,
        warehouse_config=warehouse_config,
        warehouse_catalog=warehouse_catalog,
        state_provider=state_provider,
        state_config=state_config,
        catalog_provider=catalog_provider,
        secret_provider=secret_provider,
    )


def _resolve_graph_plan(
    graph_file: Path | None,
    config: SourceConfig,
    *,
    project: str,
    dataset: str,
    build_models: bool,
    selected_models: Sequence[str] | None,
    catalog_output: Path | None,
    publish_dataplex: bool,
) -> GraphExecutionPlan | None:
    if graph_file is None:
        return None
    if build_models or selected_models is not None:
        raise ClickException("Graph pipelines do not accept --build-models or --select-model")
    if catalog_output is not None or publish_dataplex:
        raise ClickException("Graph metadata publication is not supported yet")
    try:
        graph = load_graph_for_execution(graph_file)
        return plan_graph_execution(
            graph,
            config,
            project=project or "dander-dry-run",
            dataset=dataset,
        )
    except GraphRuntimeError as error:
        raise ClickException(str(error)) from error


def _verify_safety(options: RunOptions, resolved: _ResolvedRun) -> None:
    if options.sandbox:
        try:
            SandboxDataset().prepare(resolved.project, resolved.dataset)
        except SandboxSafetyError as error:
            raise ClickException(str(error)) from error
    elif options.guarded_free_tier:
        try:
            GuardedFreeTierVerifier().require_guarded(
                resolved.project,
                budget_name=options.budget_name,
            )
        except SandboxSafetyError as error:
            raise ClickException(str(error)) from error


def _build_executor(options: RunOptions, resolved: _ResolvedRun) -> PipelineExecutor:
    secrets = build_secret_store("environment" if options.sandbox else resolved.secret_provider)
    auth = build_auth(resolved.source_config, secrets)
    try:
        source_adapter = build_source_adapter(
            resolved.source_config,
            auth,
            plugin_registry=resolved.plugin_registry,
        )
    except ConnectorPluginError as error:
        raise ClickException(str(error)) from error

    stores = _build_control_stores(options, resolved)
    dataplex_publisher = (
        build_catalog_publisher(
            provider_id=resolved.catalog_provider,
            project=resolved.project,
            location=options.dataplex_location,
        )
        if resolved.publish_dataplex
        else None
    )
    warehouse = _build_warehouse_runtime(resolved)
    ingestion = _build_ingestion_runner(options, resolved, source_adapter, warehouse, stores)
    transform_runner = _build_transform_runner(resolved, warehouse)
    return PipelineExecutor(
        pipeline_id=resolved.pipeline_id,
        source_config=resolved.source_config,
        ingestion=ingestion,
        history=stores.history,
        project=resolved.warehouse_catalog,
        models_dir=(
            resolved.graph_file.parent if resolved.graph_file is not None else options.models_dir
        ),
        selected_models=(None if resolved.graph_plan is not None else resolved.selected_models),
        build_models=resolved.graph_plan is not None or resolved.build_models,
        transform_runner=transform_runner,
        metadata_store=stores.metadata,
        registry_output=options.catalog_output,
        dataplex_publisher=dataplex_publisher,
        leases=stores.leases,
    )


def _build_control_stores(options: RunOptions, resolved: _ResolvedRun) -> _ControlStores:
    if options.sandbox:
        return _ControlStores(
            history=SqliteRunHistoryStore(options.state_path),
            leases=SqliteLeaseStore(options.state_path),
            watermarks=SqliteWatermarkStore(options.state_path),
            metadata=(
                SqliteMetadataStore(options.state_path)
                if resolved.project_pipeline and resolved.graph_plan is None
                else None
            ),
        )
    runtime = _build_state_runtime(resolved)
    runtime.migrator.migrate()
    return _ControlStores(
        history=runtime.history,
        leases=runtime.leases,
        watermarks=runtime.watermarks,
        metadata=runtime.metadata,
    )


def _build_ingestion_runner(
    options: RunOptions,
    resolved: _ResolvedRun,
    source: Source,
    warehouse: WarehouseRuntime,
    stores: _ControlStores,
) -> PipelineRunner:
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=options.sandbox,
        batch_rows=options.batch_rows,
        schema_evolution=(
            SchemaEvolution.ADDITIVE if resolved.project_pipeline else SchemaEvolution.STRICT
        ),
    )
    return PipelineRunner(
        source=source,
        writer=writer,
        watermarks=stores.watermarks,
        project=resolved.warehouse_catalog,
        dataset=resolved.dataset,
        resume_from_watermark=not options.sandbox,
        batch_rows=options.batch_rows,
        endpoint_names=(
            resolved.graph_plan.bindings.endpoint_names if resolved.graph_plan is not None else None
        ),
        target_fence=warehouse.target_fence,
    )


def _build_transform_runner(
    resolved: _ResolvedRun,
    warehouse: WarehouseRuntime,
) -> WarehouseTransformRunner | None:
    return warehouse.transforms.build_transform_runner(
        graph_plan=resolved.graph_plan,
        build_models=resolved.build_models,
    )


def _build_warehouse_runtime(resolved: _ResolvedRun) -> WarehouseRuntime:
    registry = default_provider_registry()
    try:
        config = registry.parse(
            ProviderKind.WAREHOUSE,
            resolved.warehouse_config,
        )
        runtime = registry.build(
            ProviderKind.WAREHOUSE,
            config,
            context={"project": resolved.project},
        )
    except ProviderFactoryError as error:
        raise ClickException(str(error)) from error
    if not isinstance(runtime, WarehouseRuntime):
        raise ClickException("Selected warehouse provider returned an invalid runtime")
    return runtime


def _build_state_runtime(resolved: _ResolvedRun) -> StateRuntime:
    _require_executable_state_pair(
        state_provider=resolved.state_provider,
        warehouse_provider=resolved.warehouse_provider,
    )
    registry = default_provider_registry()
    try:
        config = registry.parse(
            ProviderKind.STATE,
            resolved.state_config,
        )
        runtime = registry.build(
            ProviderKind.STATE,
            config,
            context={
                "project": resolved.project,
                "raw_dataset": resolved.dataset,
                "metadata_dataset": resolved.metadata_dataset,
                "project_pipeline": resolved.project_pipeline,
                "metadata_enabled": (resolved.project_pipeline and resolved.graph_plan is None),
            },
        )
    except ProviderFactoryError as error:
        raise ClickException(str(error)) from error
    if not isinstance(runtime, StateRuntime):
        raise ClickException("Selected state provider returned an invalid runtime")
    return runtime


def _require_executable_state_pair(*, state_provider: str, warehouse_provider: str) -> None:
    try:
        load_runtime_compatibility().require_executable(
            state=state_provider,
            warehouse=warehouse_provider,
        )
    except CompatibilityError as error:
        raise ClickException(str(error)) from error


def _requires_gcp_project(resolved: _ResolvedRun) -> bool:
    """Return whether one selected runtime profile needs a GCP project identifier."""
    return any(
        (
            resolved.warehouse_provider == "bigquery",
            resolved.state_provider == "bigquery",
            resolved.catalog_provider == "dataplex",
            resolved.secret_provider == "gcp_secret_manager",
        )
    )


def build_source_adapter(
    config: SourceConfig,
    auth: AuthStrategy,
    *,
    plugin_registry: ConnectorPluginRegistry | None = None,
) -> Source:
    """Select the extraction implementation declared by the connector."""
    registry = plugin_registry or load_connector_plugins({})
    return registry.build_source(config, auth)


def build_auth(
    config: SourceConfig,
    secrets: SecretStoreProvider,
) -> AuthStrategy:
    """Construct a supported authentication strategy from connector metadata."""
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


def _render_dry_run(options: RunOptions, resolved: _ResolvedRun, *, console: Console) -> None:
    _print_plan(
        resolved.source_config.name,
        resolved.project,
        resolved.dataset,
        _selected_endpoints(resolved.source_config, resolved.graph_plan),
        console=console,
        sandbox=options.sandbox,
        guarded_free_tier=options.guarded_free_tier,
        batch_rows=options.batch_rows,
    )
    if resolved.graph_plan is not None:
        _print_graph_plan(resolved.graph_plan, project=resolved.project, console=console)


def _print_plan(
    source: str,
    project: str,
    dataset: str,
    endpoints: Sequence[Endpoint],
    *,
    console: Console,
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
        if sandbox:
            mode = "REPLACE (sandbox)"
        elif guarded_free_tier:
            mode = "SCD1 (guarded billing)"
        else:
            mode = "SCD1"
        table.add_row(
            endpoint.name,
            f"{project or '<unset>'}.{dataset}.{source}_{endpoint.name}",
            mode,
        )
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


def _print_graph_plan(
    plan: GraphExecutionPlan,
    *,
    project: str,
    console: Console,
) -> None:
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


def _render_result(
    result: PipelineExecutionResult,
    graph_plan: GraphExecutionPlan | None,
    *,
    console: Console,
) -> None:
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
