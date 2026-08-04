"""Focused tests for the fixed deployed-job operations bridge."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from dander.pipeline.graph_deployment import GraphDeploymentPreview
from dander.pipeline.graph_operations import (
    GraphOperationBinding,
    GraphOperationConflictError,
    GraphOperationRevisionError,
    GraphOperations,
    GraphOperationValidationError,
)
from dander.pipeline.graph_service import GraphDocumentStore
from dander.state import RunRecord, RunStage, RunStatus

if TYPE_CHECKING:
    from pathlib import Path


_CONNECTOR = """
name: source
base_url: https://example.test
auth_strategy: none
endpoints:
  - name: records
    path: /records
    primary_key: [id]
    raw_schema:
      - name: id
        type: STRING
        mode: REQUIRED
""".strip()

_GRAPH = """
name: records
nodes:
  - id: records
    type: source
    name: Records
    config:
      connector: source
      endpoint: records
    fields:
      - name: id
        type: STRING
  - id: target
    type: target
    name: Target
    config:
      writer:
        write_mode: replace
        destination:
          dataset: staging
          table: graph_records
          business_key: []
    fields:
      - name: id
        type: STRING
edges:
  - from: records
    to: target
    mappings:
      - source: id
        target: id
""".strip()

_MANIFEST = """
version: 1
platform:
  region: us-central1
pipelines:
  graph_records:
    source: source
    graph: graphs/records.yaml
    models: []
    build_models: false
    resources:
      job: dander-graph-records
""".strip()


def _project(tmp_path: Path) -> tuple[GraphOperationBinding, GraphDocumentStore]:
    (tmp_path / "connectors").mkdir()
    (tmp_path / "graphs").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "connectors" / "source.yaml").write_text(_CONNECTOR, encoding="utf-8")
    graph_file = tmp_path / "graphs" / "records.yaml"
    graph_file.write_text(_GRAPH, encoding="utf-8")
    project_config = tmp_path / "dander.yaml"
    project_config.write_text(_MANIFEST, encoding="utf-8")
    binding = GraphOperationBinding.from_project(
        graph_file=graph_file,
        project_config=project_config,
        pipeline_id="graph_records",
        project="proof-project",
    )
    return binding, GraphDocumentStore(graph_file)


@dataclass
class _History:
    records: tuple[RunRecord, ...] = ()
    calls: list[tuple[int, str | None]] = field(default_factory=list)

    def recent(
        self,
        *,
        limit: int = 20,
        pipeline_id: str | None = None,
    ) -> tuple[RunRecord, ...]:
        self.calls.append((limit, pipeline_id))
        return self.records


def _terminal_execution(name: str = "dander-graph-records-abcde") -> dict[str, object]:
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [{"type": "Completed", "status": "True"}],
            "startTime": "2026-08-03T12:00:00Z",
            "completionTime": "2026-08-03T12:01:00Z",
            "succeededCount": 1,
            "failedCount": 0,
            "logUri": "https://console.cloud.google.com/run/jobs/executions/details/example",
        },
    }


def _active_execution(name: str = "dander-graph-records-older") -> dict[str, object]:
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [
                {"type": "Started", "status": "True"},
                {"type": "Completed", "status": "Unknown"},
            ]
        },
    }


def test_binding_is_derived_from_manifest_and_requires_exact_graph(tmp_path: Path) -> None:
    binding, _ = _project(tmp_path)

    assert binding.as_dict() == {
        "project": "proof-project",
        "pipeline_id": "graph_records",
        "region": "us-central1",
        "job_name": "dander-graph-records",
    }

    other_graph = tmp_path / "graphs" / "other.yaml"
    other_graph.write_text(_GRAPH, encoding="utf-8")
    with pytest.raises(GraphOperationValidationError, match="does not match"):
        GraphOperationBinding.from_project(
            graph_file=other_graph,
            project_config=tmp_path / "dander.yaml",
            pipeline_id="graph_records",
            project="proof-project",
        )


def test_validate_requires_the_opened_graph_revision(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)
    operations = GraphOperations(binding, command_runner=lambda _args: "[]", run_history=_History())

    with pytest.raises(GraphOperationRevisionError, match="changed"):
        operations.validate(store, expected_revision="0" * 64)

    revision = store.load().revision
    result = operations.validate(store, expected_revision=revision)
    assert result["valid"] is True
    assert result["revision"] == revision


def test_deployment_preview_is_explicit_and_revision_bound(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)

    class _Previewer:
        def preview(
            self,
            _store: GraphDocumentStore,
            *,
            expected_revision: str,
        ) -> GraphDeploymentPreview:
            return GraphDeploymentPreview(
                revision=expected_revision,
                candidate_image=(
                    "us-central1-docker.pkg.dev/proof-project/dander/dander@sha256:" + "a" * 64
                ),
                plan_sha256="b" * 64,
                plan_summary="Plan: 0 to add, 1 to change, 0 to destroy.",
                plan_text="exact human plan",
                affected_jobs=("dander-graph-records",),
            )

    disabled = GraphOperations(
        binding,
        command_runner=lambda _args: "[]",
        run_history=_History(),
    )
    with pytest.raises(GraphOperationConflictError, match="disabled"):
        disabled.preview_deployment(store, expected_revision=store.load().revision)

    operations = GraphOperations(
        binding,
        command_runner=lambda _args: "[]",
        run_history=_History(),
        deployment_previewer=_Previewer(),
    )
    revision = store.load().revision
    preview = operations.preview_deployment(store, expected_revision=revision)

    assert preview.revision == revision
    assert operations.status(store)["deployment_preview_enabled"] is True


def test_trigger_uses_fixed_async_job_and_blocks_until_it_is_observed_terminal(
    tmp_path: Path,
) -> None:
    binding, store = _project(tmp_path)
    calls: list[tuple[str, ...]] = []
    exact_execution_reads = 0

    def run(args: tuple[str, ...]) -> str:
        nonlocal exact_execution_reads
        calls.append(args)
        if args[1:4] == ("run", "jobs", "execute"):
            return json.dumps({"metadata": {"name": "dander-graph-records-abcde"}})
        if any(value.startswith("--filter=metadata.name=") for value in args):
            exact_execution_reads += 1
            return "[]" if exact_execution_reads == 1 else json.dumps([_terminal_execution()])
        return "[]"

    operations = GraphOperations(binding, command_runner=run, run_history=_History())
    revision = store.load().revision

    submitted = operations.trigger(store, expected_revision=revision)
    assert submitted.state == "starting"
    assert calls[-1] == (
        "gcloud",
        "run",
        "jobs",
        "execute",
        "dander-graph-records",
        "--project",
        "proof-project",
        "--region",
        "us-central1",
        "--async",
        "--format=json",
    )
    with pytest.raises(GraphOperationConflictError, match="still being submitted"):
        operations.trigger(store, expected_revision=revision)

    assert operations.status(store)["execution"] == {
        "name": "dander-graph-records-abcde",
        "state": "starting",
        "started_at": None,
        "completed_at": None,
        "succeeded_count": 0,
        "failed_count": 0,
        "log_uri": None,
    }
    assert operations.status(store)["execution"] == {
        "name": "dander-graph-records-abcde",
        "state": "succeeded",
        "started_at": "2026-08-03T12:00:00Z",
        "completed_at": "2026-08-03T12:01:00Z",
        "succeeded_count": 1,
        "failed_count": 0,
        "log_uri": "https://console.cloud.google.com/run/jobs/executions/details/example",
    }

    operations.trigger(store, expected_revision=revision)


def test_trigger_rejects_any_active_execution_even_when_it_is_not_latest(
    tmp_path: Path,
) -> None:
    binding, store = _project(tmp_path)

    def run(args: tuple[str, ...]) -> str:
        if any("status.conditions.status=Unknown" in value for value in args):
            return json.dumps([_active_execution()])
        if "--limit=1" in args:
            return json.dumps([_terminal_execution("dander-graph-records-newer")])
        raise AssertionError(f"Unexpected command: {args}")

    operations = GraphOperations(binding, command_runner=run, run_history=_History())

    with pytest.raises(GraphOperationConflictError, match="already active"):
        operations.trigger(store, expected_revision=store.load().revision)


def test_status_combines_deployment_and_latest_dander_run(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)
    record = RunRecord(
        run_id="run-1",
        pipeline_id="graph_records",
        source="source",
        status=RunStatus.SUCCEEDED,
        stage=RunStage.COMPLETE,
        started_at="2026-08-03T12:00:00+00:00",
        finished_at="2026-08-03T12:01:00+00:00",
        endpoints=1,
        extracted=21,
        affected=21,
        models=0,
        assertions=0,
        assets=0,
        failure_stage=None,
    )
    history = _History((record,))

    def run(args: tuple[str, ...]) -> str:
        if any("status.conditions.status=Unknown" in value for value in args):
            return "[]"
        return json.dumps([_terminal_execution()])

    status = GraphOperations(binding, command_runner=run, run_history=history).status(store)

    assert status["execution"] == _expected_terminal_projection()
    assert status["run"] == {
        "run_id": "run-1",
        "pipeline_id": "graph_records",
        "source": "source",
        "status": "succeeded",
        "stage": "complete",
        "started_at": "2026-08-03T12:00:00+00:00",
        "finished_at": "2026-08-03T12:01:00+00:00",
        "endpoints": 1,
        "extracted": 21,
        "affected": 21,
        "models": 0,
        "assertions": 0,
        "assets": 0,
        "failure_stage": None,
    }
    assert history.calls == [(1, "graph_records")]


def _expected_terminal_projection() -> dict[str, object]:
    return {
        "name": "dander-graph-records-abcde",
        "state": "succeeded",
        "started_at": "2026-08-03T12:00:00Z",
        "completed_at": "2026-08-03T12:01:00Z",
        "succeeded_count": 1,
        "failed_count": 0,
        "log_uri": "https://console.cloud.google.com/run/jobs/executions/details/example",
    }
