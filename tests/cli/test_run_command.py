"""Non-network composition coverage for ``dander run``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from click import ClickException
from rich.console import Console
from typer.testing import CliRunner

import dander.cli.run_command as run_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.security import NoAuth
from dander.state import StateCapabilities, StateMigration, StateRuntime
from dander.warehouse import RelationRef
from dander.writer import SchemaEvolution

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

    from dander.catalog import MetadataStore
    from dander.ingestion import SourceConfig
    from dander.state import LeaseStore, RunHistoryStore, WatermarkStore

_REPO_ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class _Built:
    name: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class _ResolvedWarehouse(Protocol):
    warehouse_provider: str
    warehouse_config: dict[str, object]


class _ResolvedState(Protocol):
    state_provider: str
    project: str
    dataset: str
    metadata_dataset: str
    state_catalog: str | None
    state_namespace: str | None


class _Migrator:
    migrations = (StateMigration(version=1, name="existing_control_tables"),)

    def current_version(self) -> int:
        return 0

    def migrate(self) -> int:
        self.calls += 1
        return 1

    def __init__(self) -> None:
        self.calls = 0


def test_hosted_project_run_wires_runtime_without_network(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def recording_factory(name: str) -> Callable[..., _Built]:
        def build(*args: object, **kwargs: object) -> _Built:
            component = _Built(name=name, args=args, kwargs=kwargs)
            captured[name] = component
            return component

        return build

    def build_auth(config: SourceConfig, secrets: object) -> NoAuth:
        assert config.name == "greenhouse_job_board"
        assert isinstance(secrets, _Built)
        return NoAuth()

    def build_source(
        config: SourceConfig,
        auth: object,
        *,
        plugin_registry: object,
    ) -> _Built:
        assert config.name == "greenhouse_job_board"
        assert isinstance(auth, NoAuth)
        assert plugin_registry is not None
        return _Built(name="source", args=(), kwargs={})

    class _Executor:
        def __init__(self, **kwargs: object) -> None:
            captured["executor"] = kwargs

        def execute(self) -> PipelineExecutionResult:
            return PipelineExecutionResult(
                run_id="run-123",
                pipeline_id="greenhouse_jobs",
                ingestion=PipelineRunResult(
                    run_id="run-123",
                    source="greenhouse_job_board",
                    endpoints=(
                        EndpointRunResult(
                            endpoint="jobs",
                            extracted=2,
                            affected=2,
                            committed_cursor=None,
                        ),
                    ),
                ),
                models=("stg_greenhouse__jobs",),
                assertions=1,
                assets=1,
            )

    class _WriterFactory:
        def build_ingestion_writer(self, **kwargs: object) -> _Built:
            component = _Built(name="writer", args=(), kwargs=kwargs)
            captured["writer"] = component
            return component

    class _TransformFactory:
        def build_transform_runner(self, **kwargs: object) -> _Built:
            component = _Built(name="transform", args=(), kwargs=kwargs)
            captured["transform"] = component
            return component

    class _Warehouse:
        writers = _WriterFactory()
        transforms = _TransformFactory()
        target_fence = _Built(name="target-fence", args=(), kwargs={})

    def build_warehouse(resolved: _ResolvedWarehouse) -> _Warehouse:
        assert resolved.warehouse_provider == "bigquery"
        assert resolved.warehouse_config == {"provider": "bigquery", "location": "US"}
        return _Warehouse()

    def build_state(resolved: _ResolvedState) -> StateRuntime:
        assert resolved.state_provider == "bigquery"
        assert resolved.project == "unit-project"
        assert resolved.dataset == "landing"
        assert resolved.metadata_dataset == "dander_meta"
        assert resolved.state_catalog == "unit-project"
        assert resolved.state_namespace == "landing"
        migrator = _Migrator()
        captured["migrator"] = migrator
        for name in ("history", "leases", "metadata", "watermarks"):
            captured[name] = _Built(name=name, args=(), kwargs={})
        return StateRuntime(
            provider_id="bigquery",
            leases=cast("LeaseStore", captured["leases"]),
            watermarks=cast("WatermarkStore", captured["watermarks"]),
            history=cast("RunHistoryStore", captured["history"]),
            metadata=cast("MetadataStore", captured["metadata"]),
            migrator=migrator,
            capabilities=StateCapabilities(
                provider_id="bigquery",
                schema_version=1,
                server_time=True,
                atomic_leases=True,
                monotonic_fencing=True,
                atomic_watermark_cas=True,
                interrupted_run_reconciliation=True,
            ),
        )

    def build_secrets(provider_id: str) -> _Built:
        assert provider_id == "gcp_secret_manager"
        return recording_factory("secrets")()

    monkeypatch.setattr(run_module, "build_secret_store", build_secrets)
    monkeypatch.setattr(run_module, "build_auth", build_auth)
    monkeypatch.setattr(run_module, "build_source_adapter", build_source)
    monkeypatch.setattr(run_module, "PipelineRunner", recording_factory("ingestion"))
    monkeypatch.setattr(run_module, "_build_warehouse_runtime", build_warehouse)
    monkeypatch.setattr(run_module, "_build_state_runtime", build_state)
    monkeypatch.setattr(run_module, "PipelineExecutor", _Executor)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse_jobs",
            "--project",
            "unit-project",
            "--dataset",
            "landing",
            "--config",
            str(_REPO_ROOT / "dander.yaml"),
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
            "--models-dir",
            str(_REPO_ROOT / "models"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dander run run-123" in result.output
    executor = cast("dict[str, object]", captured["executor"])
    assert executor["pipeline_id"] == "greenhouse_jobs"
    assert executor["catalog"] == "unit-project"
    assert executor["raw_namespace"] == "landing"
    assert executor["source_relations"] == {
        "jobs": RelationRef(
            catalog="unit-project",
            namespace="landing",
            name="greenhouse_job_board_jobs",
        )
    }
    assert executor["selected_models"] == ("stg_greenhouse__jobs",)
    assert executor["build_models"] is True
    assert executor["history"] == captured["history"]
    assert executor["leases"] == captured["leases"]
    assert executor["metadata_store"] == captured["metadata"]
    assert executor["transform_runner"] == captured["transform"]

    ingestion = cast("_Built", executor["ingestion"])
    assert ingestion.kwargs["endpoint_relations"] == executor["source_relations"]
    assert ingestion.kwargs["resume_from_watermark"] is True
    assert ingestion.kwargs["batch_rows"] == 10_000
    assert ingestion.kwargs["watermarks"] == captured["watermarks"]
    assert ingestion.kwargs["target_fence"] == _Warehouse.target_fence
    writer = cast("_Built", ingestion.kwargs["writer"])
    assert writer.kwargs == {
        "sandbox": False,
        "batch_rows": 10_000,
        "schema_evolution": SchemaEvolution.ADDITIVE,
    }
    migrator = cast("_Migrator", captured["migrator"])
    assert migrator.calls == 1


@pytest.mark.parametrize(
    ("sandbox", "guarded_free_tier", "message"),
    [
        (True, False, "--sandbox is available only with a BigQuery warehouse"),
        (
            False,
            True,
            "--guarded-free-tier is available only with a BigQuery warehouse",
        ),
    ],
)
def test_postgresql_rejects_bigquery_safety_before_external_clients(
    monkeypatch: MonkeyPatch,
    *,
    sandbox: bool,
    guarded_free_tier: bool,
    message: str,
) -> None:
    options = SimpleNamespace(
        sandbox=sandbox,
        guarded_free_tier=guarded_free_tier,
        dry_run=False,
    )
    resolved = SimpleNamespace(
        warehouse_provider="postgresql",
        publish_dataplex=False,
        catalog_provider="none",
    )
    monkeypatch.setattr(run_module, "_resolve_run", lambda _options: resolved)

    class _ForbiddenExternalClient:
        def __init__(self) -> None:
            raise AssertionError("provider guard must run before constructing a GCP client")

    monkeypatch.setattr(run_module, "SandboxDataset", _ForbiddenExternalClient)
    monkeypatch.setattr(run_module, "GuardedFreeTierVerifier", _ForbiddenExternalClient)

    with pytest.raises(ClickException, match=message):
        run_module.execute_run(cast("Any", options), console=Console())


def test_postgresql_dataplex_publication_fails_before_executor(
    monkeypatch: MonkeyPatch,
) -> None:
    options = SimpleNamespace(sandbox=False, guarded_free_tier=False, dry_run=False)
    resolved = SimpleNamespace(
        warehouse_provider="postgresql",
        publish_dataplex=True,
        catalog_provider="dataplex",
    )
    monkeypatch.setattr(run_module, "_resolve_run", lambda _options: resolved)
    monkeypatch.setattr(
        run_module,
        "_build_executor",
        lambda *_args, **_kwargs: pytest.fail("executor must not be constructed"),
    )

    with pytest.raises(ClickException, match="requires a BigQuery warehouse"):
        run_module.execute_run(cast("Any", options), console=Console())


def test_bigquery_state_location_stays_independent_from_postgresql_warehouse() -> None:
    resolved = SimpleNamespace(
        state_catalog="gcp-control-project",
        state_namespace="dander_state",
        metadata_namespace="dander_meta",
        project_pipeline=True,
        graph_plan=None,
        catalog="postgres_database",
        raw_namespace="landing",
    )

    context = run_module._state_runtime_context(cast("Any", resolved))

    assert context["catalog"] == "gcp-control-project"
    assert context["raw_namespace"] == "dander_state"
    assert context["metadata_namespace"] == "dander_meta"


def test_snowflake_coordinates_resolve_from_database_and_schema(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(
        """\
version: 2
pipelines:
  greenhouse_jobs:
    source: greenhouse_job_board
    models: [stg_greenhouse__jobs]
""",
        encoding="utf-8",
    )
    platforms_path.write_text(
        """\
version: 1
platforms:
  snowflake:
    warehouse:
      provider: snowflake
      account: org-account
      user: dander_user
      database: DANDER_DB
      schema: LANDING
      warehouse: DANDER_WH
      auth:
        method: oauth
        token_env: DANDER_SNOWFLAKE_TOKEN
    state:
      provider: bigquery
    catalog:
      provider: none
    secrets:
      provider: environment
deployments:
  snowflake_kubernetes:
    platform: snowflake
    launcher:
      provider: kubernetes
      context: test-context
    safety:
      require_guarded_free_tier: false
    pipelines:
      greenhouse_jobs:
        paused: true
""",
        encoding="utf-8",
    )
    options = run_module.RunOptions(
        pipeline_or_source="greenhouse_jobs",
        project="control-project",
        dataset=None,
        connectors_dir=_REPO_ROOT / "connectors",
        project_config=project_path,
        platforms_config=platforms_path,
        deployment="snowflake_kubernetes",
        dry_run=True,
        sandbox=False,
        guarded_free_tier=False,
        batch_rows=10_000,
        budget_name="dander-sandbox-budget",
        state_path=tmp_path / "state.sqlite3",
        build_models=False,
        models_dir=_REPO_ROOT / "models",
        selected_models=None,
        catalog_output=None,
        publish_dataplex=False,
        dataplex_location="us-central1",
    )

    resolved = run_module._resolve_run(options)

    assert resolved.catalog == "DANDER_DB"
    assert resolved.raw_namespace == "LANDING"
    assert resolved.endpoint_relations == {
        "jobs": RelationRef(
            catalog="DANDER_DB",
            namespace="LANDING",
            name="greenhouse_job_board_jobs",
        )
    }
    assert resolved.state_catalog == "control-project"
    assert resolved.state_namespace == "raw"


def test_only_unsupported_postgresql_state_bigquery_warehouse_pair_fails_closed() -> None:
    with pytest.raises(ClickException, match="BigQuery write mode"):
        run_module._require_executable_state_pair(
            state_provider="postgresql",
            warehouse_provider="bigquery",
        )

    run_module._require_executable_state_pair(
        state_provider="bigquery",
        warehouse_provider="bigquery",
    )
    run_module._require_executable_state_pair(
        state_provider="postgresql",
        warehouse_provider="postgresql",
    )
    run_module._require_executable_state_pair(
        state_provider="bigquery",
        warehouse_provider="postgresql",
    )
    run_module._require_executable_state_pair(
        state_provider="bigquery",
        warehouse_provider="redshift",
    )
    run_module._require_executable_state_pair(
        state_provider="postgresql",
        warehouse_provider="redshift",
    )
