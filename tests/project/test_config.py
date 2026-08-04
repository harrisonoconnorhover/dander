"""Project manifest validation and Terraform expansion tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dander.ingestion import IngestionEngine, SourceConfig
from dander.project import ProjectConfigError, load_project_config

_VALID_CONNECTOR = """
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

_VALID_GRAPH = """
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


def test_repository_manifest_defines_five_additive_hosted_pipelines() -> None:
    project = load_project_config(Path("dander.yaml"))
    project.validate_references(Path.cwd())

    assert project.platform.region == "us-central1"
    assert project.platform.bigquery_location == "US"
    assert project.platform.runtime.model_dump() == {
        "cpu": 1,
        "memory": "512Mi",
        "timeout_seconds": 300,
        "max_retries": 1,
        "batch_rows": 10_000,
    }
    assert project.platform.safety.require_guarded_free_tier is True
    assert project.plugins == {}
    expanded = project.terraform_pipelines()
    assert set(expanded) == {
        "greenhouse_jobs",
        "greenhouse_jobs_graph",
        "hubspot_companies",
        "salesforce_accounts",
        "servicenow_incidents",
    }
    assert expanded["greenhouse_jobs"]["job_name"] == "dander-greenhouse-public"
    assert expanded["greenhouse_jobs"]["secret_env"] == {}
    assert expanded["greenhouse_jobs_graph"]["job_name"] == "dander-greenhouse-graph"
    assert expanded["greenhouse_jobs_graph"]["models"] == []
    assert expanded["greenhouse_jobs_graph"]["build_models"] is False
    assert expanded["greenhouse_jobs_graph"]["paused"] is True
    assert expanded["greenhouse_jobs_graph"]["secret_env"] == {}
    assert expanded["hubspot_companies"]["job_name"] == "dander-hubspot-companies"
    assert expanded["hubspot_companies"]["secret_env"] == {
        "HUBSPOT_PRIVATE_APP_TOKEN": "hubspot-private-app-token"
    }
    assert expanded["salesforce_accounts"]["job_name"] == "dander-salesforce-accounts"
    assert expanded["salesforce_accounts"]["paused"] is False
    assert expanded["salesforce_accounts"]["secret_env"] == {
        "SALESFORCE_EXTERNAL_CLIENT_APP_ID": "salesforce-external-client-app-id",
        "SALESFORCE_EXTERNAL_CLIENT_APP_PRIVATE_KEY": (
            "salesforce-external-client-app-private-key"
        ),
    }
    assert expanded["servicenow_incidents"]["job_name"] == "dander-servicenow-incidents"
    assert expanded["servicenow_incidents"]["paused"] is False
    assert expanded["servicenow_incidents"]["secret_env"] == {
        "SERVICENOW_CLIENT_ID": "servicenow-client-id",
        "SERVICENOW_CLIENT_SECRET": "servicenow-client-secret",
    }


def test_manifest_accepts_exact_plugin_pins_and_connector_engines_remain_compatible(
    tmp_path: Path,
) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
plugins:
  salesforce:
    distribution: dander-connector-salesforce
    version: 0.1.0rc1
pipelines:
  example:
    source: salesforce
    models: [example]
""".strip(),
        encoding="utf-8",
    )

    project = load_project_config(config)
    builtin = SourceConfig(
        name="builtin",
        base_url="https://example.test",
        engine="salesforce_bulk2",
        auth_strategy="none",
    )
    plugin = SourceConfig(
        name="plugin",
        base_url="https://example.test",
        engine="custom_engine",
        auth_strategy="none",
    )

    assert project.plugins["salesforce"].version == "0.1.0rc1"
    assert builtin.engine is IngestionEngine.SALESFORCE_BULK2
    assert plugin.engine == "custom_engine"


@pytest.mark.parametrize("version", ["^0.1.0", ">=0.1.0", "latest"])
def test_manifest_rejects_non_exact_plugin_versions(tmp_path: Path, version: str) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        f"""
version: 1
plugins:
  example:
    distribution: dander-connector-example
    version: {version!r}
pipelines:
  example:
    source: example
    models: [example]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match="plugins.example.version"):
        load_project_config(config)


def test_generated_resource_names_are_stable_and_bounded(tmp_path: Path) -> None:
    config = tmp_path / "dander.yaml"
    connector_dir = tmp_path / "connectors"
    model_dir = tmp_path / "models"
    connector_dir.mkdir()
    model_dir.mkdir()
    (connector_dir / "source.yaml").write_text(_VALID_CONNECTOR, encoding="utf-8")
    (model_dir / "model.sql").write_text("SELECT 1\n", encoding="utf-8")
    config.write_text(
        """
version: 1
pipelines:
  long_pipeline_identifier_for_bounded_names:
    source: source
    models: [model]
""".strip(),
        encoding="utf-8",
    )

    project = load_project_config(config)
    project.validate_references(tmp_path)
    expanded = project.terraform_pipelines()["long_pipeline_identifier_for_bounded_names"]
    assert len(str(expanded["job_name"])) <= 63
    assert len(str(expanded["runtime_service_account_id"])) <= 30
    assert len(str(expanded["scheduler_service_account_id"])) <= 30


def test_missing_model_is_reported_by_pipeline_structure_only(tmp_path: Path) -> None:
    (tmp_path / "connectors").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "connectors" / "source.yaml").write_text(
        _VALID_CONNECTOR,
        encoding="utf-8",
    )
    config = tmp_path / "dander.yaml"
    config.write_text(
        "version: 1\npipelines:\n  example:\n    source: source\n    models: [missing]\n",
        encoding="utf-8",
    )

    project = load_project_config(config)
    with pytest.raises(ProjectConfigError, match="Pipeline 'example'.*missing model 'missing'"):
        project.validate_references(tmp_path)


def test_graph_pipeline_validates_without_legacy_models(tmp_path: Path) -> None:
    (tmp_path / "connectors").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "graphs").mkdir()
    (tmp_path / "connectors" / "source.yaml").write_text(
        _VALID_CONNECTOR,
        encoding="utf-8",
    )
    (tmp_path / "graphs" / "records.yaml").write_text(_VALID_GRAPH, encoding="utf-8")
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
pipelines:
  graph_records:
    source: source
    graph: graphs/records.yaml
    models: []
    build_models: false
""".strip(),
        encoding="utf-8",
    )

    project = load_project_config(config)
    project.validate_references(tmp_path)

    expanded = project.terraform_pipelines()["graph_records"]
    assert expanded["models"] == []
    assert expanded["build_models"] is False


def test_graph_pipeline_rejects_legacy_model_execution(tmp_path: Path) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
pipelines:
  graph_records:
    source: source
    graph: graphs/records.yaml
    models: [legacy_model]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match="pipelines.graph_records"):
        load_project_config(config)


def test_hosted_pipeline_requires_raw_schema_for_every_endpoint(tmp_path: Path) -> None:
    (tmp_path / "connectors").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "connectors" / "source.yaml").write_text(
        """
name: source
base_url: https://example.test
auth_strategy: none
endpoints:
  - name: records
    path: /records
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "models" / "model.sql").write_text("SELECT 1\n", encoding="utf-8")
    config = tmp_path / "dander.yaml"
    config.write_text(
        "version: 1\npipelines:\n  example:\n    source: source\n    models: [model]\n",
        encoding="utf-8",
    )

    project = load_project_config(config)

    with pytest.raises(ProjectConfigError, match="endpoint 'records'.*declare raw_schema"):
        project.validate_references(tmp_path)


def test_project_config_rejects_literal_secret_values(tmp_path: Path) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
pipelines:
  example:
    source: source
    models: [model]
    secrets:
      Authorization: pat-secret-value
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match="pipelines.example.secrets"):
        load_project_config(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu", 3),
        ("memory", "512MB"),
        ("timeout_seconds", 0),
        ("max_retries", 11),
        ("batch_rows", 100_001),
    ],
)
def test_project_config_rejects_invalid_runtime_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    config = tmp_path / "dander.yaml"
    config.write_text(
        f"""
version: 1
platform:
  runtime:
    {field}: {value}
pipelines:
  example:
    source: source
    models: [model]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match=rf"platform.runtime.{field}"):
        load_project_config(config)
