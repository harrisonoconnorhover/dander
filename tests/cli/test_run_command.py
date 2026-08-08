"""Non-network composition coverage for ``dander run``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from typer.testing import CliRunner

import dander.cli.run_command as run_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.security import NoAuth
from dander.writer import SchemaEvolution

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

    from dander.ingestion import SourceConfig

_REPO_ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class _Built:
    name: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class _ResolvedWarehouse(Protocol):
    warehouse_provider: str
    warehouse_location: str


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

    monkeypatch.setattr(run_module, "DefaultSecretStore", recording_factory("secrets"))
    monkeypatch.setattr(run_module, "build_auth", build_auth)
    monkeypatch.setattr(run_module, "build_source_adapter", build_source)
    monkeypatch.setattr(run_module, "BigQueryRunHistoryStore", recording_factory("history"))
    monkeypatch.setattr(run_module, "BigQueryLeaseStore", recording_factory("leases"))
    monkeypatch.setattr(run_module, "BigQueryMetadataStore", recording_factory("metadata"))
    monkeypatch.setattr(run_module, "BigQueryWatermarkStore", recording_factory("watermarks"))
    monkeypatch.setattr(run_module, "PipelineRunner", recording_factory("ingestion"))
    monkeypatch.setattr(run_module, "_build_warehouse_runtime", build_warehouse)
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
    history = cast("_Built", captured["history"])
    metadata = cast("_Built", captured["metadata"])
    assert history.kwargs["dataset"] == "dander_meta"
    assert metadata.kwargs["dataset"] == "dander_meta"
