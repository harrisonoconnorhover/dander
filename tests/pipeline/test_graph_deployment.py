"""Focused candidate-snapshot and isolated-plan tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dander.pipeline.graph_deployment import (
    GraphDeploymentError,
    GraphDeploymentPreviewer,
    GraphDeploymentSettings,
    GraphDeploymentStaleError,
)
from dander.pipeline.graph_operations import GraphOperationBinding
from dander.pipeline.graph_service import GraphDocumentStore

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

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
  safety:
    require_guarded_free_tier: true
pipelines:
  graph_records:
    source: source
    graph: graphs/records.yaml
    models: []
    build_models: false
    secrets:
      SOURCE_TOKEN: source-token
    resources:
      job: dander-graph-records
""".strip()


def _project(tmp_path: Path) -> tuple[GraphOperationBinding, GraphDocumentStore]:
    for directory in ("connectors", "graphs", "models", "infra"):
        (tmp_path / directory).mkdir()
    (tmp_path / "connectors" / "source.yaml").write_text(_CONNECTOR, encoding="utf-8")
    graph_file = tmp_path / "graphs" / "records.yaml"
    graph_file.write_text(_GRAPH, encoding="utf-8")
    config = tmp_path / "dander.yaml"
    config.write_text(_MANIFEST, encoding="utf-8")
    (tmp_path / "infra" / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    (tmp_path / "infra" / "dander-bootstrap.tfplan").write_bytes(b"operator-plan")
    binding = GraphOperationBinding.from_project(
        graph_file=graph_file,
        project_config=config,
        pipeline_id="graph_records",
        project="proof-project",
    )
    return binding, GraphDocumentStore(graph_file)


class _Publisher:
    def __init__(self, root: Path, observations: dict[str, Any]) -> None:
        self.root = root
        self.observations = observations

    def publish(self, *, project: str, region: str, tag_prefix: str = "init") -> str:
        self.observations["image_root"] = self.root
        self.observations["source_free"] = not (self.root / "src").exists()
        self.observations["dockerfile"] = (self.root / "Dockerfile").read_text()
        self.observations["graph"] = (self.root / "graphs" / "records.yaml").read_text()
        self.observations["publish"] = (project, region, tag_prefix)
        return "us-central1-docker.pkg.dev/proof-project/dander/dander@sha256:" + "a" * 64


class _Planner:
    def __init__(self, root: Path, observations: dict[str, Any]) -> None:
        self.root = root
        self.observations = observations

    def execute(self, **kwargs: object) -> Path:
        self.observations["plan_root"] = self.root
        self.observations["plan_kwargs"] = kwargs
        path = self.root / "dander-bootstrap.tfplan"
        path.write_bytes(b"isolated-plan")
        return path


def _settings(**overrides: object) -> GraphDeploymentSettings:
    values: dict[str, object] = {
        "state_bucket": "proof-project-dander-state",
        "bootstrap_service_account": "dander-bootstrap@proof-project.iam.gserviceaccount.com",
        "billing_account_id": "ABCDEF-123456-ABCDEF",
        "failure_alert_email": "operator@example.invalid",
    }
    values.update(overrides)
    return GraphDeploymentSettings(**values)  # type: ignore[arg-type]


def test_preview_builds_source_free_snapshot_and_isolates_exact_plan(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)
    observations: dict[str, Any] = {}

    preview = GraphDeploymentPreviewer(
        binding,
        _settings(secret_ids=("operator-secret", "source-token")),
        image_publisher_factory=lambda root: _Publisher(root, observations),
        terraform_planner_factory=lambda root: _Planner(root, observations),
        plan_renderer=lambda path, root: (
            "Terraform will perform the following actions:\n\n"
            "Plan: 0 to add, 1 to change, 0 to destroy.\n"
        ),
    ).preview(store, expected_revision=store.load().revision)

    assert observations["source_free"] is True
    assert "ARG DANDER_VERSION=0.4.0" in observations["dockerfile"]
    assert "RUN dander plugins install --config dander.yaml" in observations["dockerfile"]
    assert observations["graph"] == _GRAPH
    assert observations["publish"] == ("proof-project", "us-central1", "candidate")
    assert observations["plan_kwargs"]["apply"] is False
    assert observations["plan_kwargs"]["secret_ids"] == ("operator-secret", "source-token")
    assert observations["plan_kwargs"]["container_image"] == preview.candidate_image
    assert preview.plan_summary == "Plan: 0 to add, 1 to change, 0 to destroy."
    assert preview.affected_jobs == ("dander-graph-records",)
    assert not observations["image_root"].exists()
    assert not observations["plan_root"].exists()
    assert (tmp_path / "infra" / "dander-bootstrap.tfplan").read_bytes() == b"operator-plan"


def test_preview_rejects_project_change_during_build(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)

    class _ChangingPublisher(_Publisher):
        def publish(self, *, project: str, region: str, tag_prefix: str = "init") -> str:
            image = super().publish(project=project, region=region, tag_prefix=tag_prefix)
            binding.graph_file.write_text(_GRAPH.replace("Records", "Changed"), encoding="utf-8")
            return image

    with pytest.raises(GraphDeploymentStaleError, match="project changed"):
        GraphDeploymentPreviewer(
            binding,
            _settings(),
            image_publisher_factory=lambda root: _ChangingPublisher(root, {}),
            terraform_planner_factory=lambda root: _Planner(root, {}),
            plan_renderer=lambda _path, _root: "No changes.\n",
        ).preview(store, expected_revision=store.load().revision)


def test_guarded_preview_requires_billing_before_build(tmp_path: Path) -> None:
    binding, store = _project(tmp_path)
    called = False

    def publisher(_root: Path) -> _Publisher:
        nonlocal called
        called = True
        return _Publisher(_root, {})

    with pytest.raises(GraphDeploymentError, match="billing-account"):
        GraphDeploymentPreviewer(
            binding,
            _settings(billing_account_id=""),
            image_publisher_factory=publisher,
        ).preview(store, expected_revision=store.load().revision)

    assert called is False
