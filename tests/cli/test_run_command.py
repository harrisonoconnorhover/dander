"""Non-network composition coverage for ``dander run``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from click import ClickException
from typer.testing import CliRunner

import dander.cli.run_command as run_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.security import NoAuth
from dander.state import StateCapabilities, StateMigration, StateRuntime
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
    warehouse_location: str


class _ResolvedState(Protocol):
    state_provider: str
    project: str
    dataset: str
    metadata_dataset: str


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

    def build_warehouse(resolved: _ResolvedWarehouse) -> _Warehouse:
        assert resolved.warehouse_provider == "bigquery"
        assert resolved.warehouse_location == "US"
        return _Warehouse()

    def build_state(resolved: _ResolvedState) -> StateRuntime:
        assert resolved.state_provider == "bigquery"
        assert resolved.project == "unit-project"
        assert resolved.dataset == "raw"
        assert resolved.metadata_dataset == "dander_meta"
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
            "raw",
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
    assert executor["project"] == "unit-project"
    assert executor["selected_models"] == ("stg_greenhouse__jobs",)
    assert executor["build_models"] is True
    assert executor["history"] == captured["history"]
    assert executor["leases"] == captured["leases"]
    assert executor["metadata_store"] == captured["metadata"]
    assert executor["transform_runner"] == captured["transform"]

    ingestion = cast("_Built", executor["ingestion"])
    assert ingestion.kwargs["project"] == "unit-project"
    assert ingestion.kwargs["dataset"] == "raw"
    assert ingestion.kwargs["resume_from_watermark"] is True
    assert ingestion.kwargs["batch_rows"] == 10_000
    assert ingestion.kwargs["watermarks"] == captured["watermarks"]
    writer = cast("_Built", ingestion.kwargs["writer"])
    assert writer.kwargs == {
        "sandbox": False,
        "batch_rows": 10_000,
        "schema_evolution": SchemaEvolution.ADDITIVE,
    }
    migrator = cast("_Migrator", captured["migrator"])
    assert migrator.calls == 1


def test_postgresql_state_fails_closed_before_cross_backend_fencing() -> None:
    with pytest.raises(ClickException, match="cross-backend destination fence"):
        run_module._require_supported_state_pair(
            state_provider="postgresql",
            warehouse_provider="bigquery",
        )

    run_module._require_supported_state_pair(
        state_provider="bigquery",
        warehouse_provider="bigquery",
    )
