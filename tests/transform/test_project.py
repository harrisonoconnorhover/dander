"""Transform project discovery, dependency, and compilation tests."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dander.transform import TransformProject, TransformProjectError
from dander.warehouse import RelationRef


def _write_model(
    root: Path,
    name: str,
    sql: str,
    *,
    materialization: str = "view",
    dialect: str | None = None,
    tests: str = "[]",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.sql").write_text(dedent(sql))
    (root / f"{name}.yml").write_text(
        dedent(
            f"""
            model: {name}
            description: Safe test model.
            owner: data-eng
            {f"dialect: {dialect}" if dialect is not None else ""}
            materialization: {materialization}
            dataset: staging
            source_system: fixture
            sensitivity: public
            columns:
              - name: id
                type: STRING
                description: Fixture identifier.
            tests: {tests}
            """
        )
    )


def _write_variant(root: Path, name: str, dialect: str, sql: str) -> None:
    (root / f"{name}.{dialect}.sql").write_text(dedent(sql))


def test_load_orders_selected_model_dependencies_and_compiles_refs(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "base",
        "SELECT CAST(id AS STRING) AS id FROM {{ ref('raw_fixture') }}",
    )
    _write_model(
        tmp_path,
        "consumer",
        "SELECT id FROM {{ ref('base') }}",
    )

    project = TransformProject.load(tmp_path, project_id="valid-project-123")
    ordered = project.ordered(["consumer"])

    assert [model.name for model in ordered] == ["base", "consumer"]
    assert project.compile(ordered[0]) == (
        "SELECT CAST(id AS STRING) AS id FROM `valid-project-123.raw.fixture`"
    )
    assert project.compile(ordered[1]) == ("SELECT id FROM `valid-project-123.staging.base`")


def test_existing_models_default_to_exact_bigquery_sql(tmp_path: Path) -> None:
    _write_model(tmp_path, "existing", "SELECT CAST(id AS STRING) AS id")
    project = TransformProject.load(tmp_path, project_id="valid-project-123")
    model = project.models["existing"]

    assert model.metadata.dialect.value == "bigquery"
    with pytest.raises(TransformProjectError, match="cannot target postgres"):
        project.compile(model, target_dialect="postgres")


@pytest.mark.parametrize("target", ["bigquery", "snowflake", "redshift", "postgres"])
def test_portable_model_compiles_refs_for_selected_target(tmp_path: Path, target: str) -> None:
    _write_model(
        tmp_path,
        "portable_model",
        "SELECT CAST(id AS STRING) AS id FROM {{ ref('raw_fixture') }}",
        dialect="portable",
    )
    project = TransformProject.load(tmp_path, project_id="valid-project-123")

    compiled = project.compile(project.models["portable_model"], target_dialect=target)

    assert "fixture" in compiled
    assert "SELECT" in compiled


def test_exact_provider_model_compiles_only_for_declared_target(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "provider_model",
        "SELECT CAST(id AS TEXT) AS id FROM {{ ref('raw_fixture') }}",
        dialect="postgres",
    )
    project = TransformProject.load(tmp_path, project_id="valid-project-123")

    assert '"raw"."fixture"' in project.compile(
        project.models["provider_model"],
        target_dialect="postgres",
    )
    with pytest.raises(TransformProjectError, match="cannot target bigquery"):
        project.compile(project.models["provider_model"])


def test_provider_variant_uses_shared_metadata_for_selected_target(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "jobs",
        "SELECT location.name AS location_name FROM {{ ref('raw_jobs') }}",
        dialect="bigquery",
    )
    _write_variant(
        tmp_path,
        "jobs",
        "postgres",
        "SELECT location ->> 'name' AS location_name FROM {{ ref('raw_jobs') }}",
    )

    bigquery = TransformProject.load(
        tmp_path,
        catalog="valid-project-123",
        target_dialect="bigquery",
    )
    postgres = TransformProject.load(
        tmp_path,
        catalog="dander",
        target_dialect="postgres",
    )

    assert bigquery.models["jobs"].metadata.dialect.value == "bigquery"
    assert "location.name" in bigquery.compile(bigquery.models["jobs"])
    assert postgres.models["jobs"].metadata.dialect.value == "postgres"
    assert postgres.models["jobs"].metadata.columns == bigquery.models["jobs"].metadata.columns
    assert "location ->> 'name'" in postgres.compile(postgres.models["jobs"])
    assert postgres.models["jobs"].sql_path.name == "jobs.postgres.sql"


def test_provider_variant_without_base_model_fails_closed(tmp_path: Path) -> None:
    _write_variant(tmp_path, "orphan", "postgres", "SELECT 'x' AS id")

    with pytest.raises(TransformProjectError, match="has no base model"):
        TransformProject.load(tmp_path, catalog="dander", target_dialect="postgres")


def test_postgresql_project_uses_database_local_relations_by_default(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "portable_model",
        "SELECT id FROM {{ ref('raw_fixture') }}",
        dialect="portable",
    )
    project = TransformProject.load(
        tmp_path,
        project_id="dander_test",
        target_dialect="postgres",
    )

    model = project.models["portable_model"]
    assert project.relation_for_model(model) == '"staging"."portable_model"'
    assert project.relation_for_ref("raw_fixture") == '"raw"."fixture"'
    assert '"raw"."fixture"' in project.compile(model)


def test_postgresql_project_preserves_database_and_custom_raw_schema(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "portable_model",
        "SELECT id FROM {{ ref('raw_fixture') }}",
        dialect="portable",
    )
    project = TransformProject.load(
        tmp_path,
        catalog="warehouse_db",
        raw_namespace="landing",
        target_dialect="postgres",
    )

    assert project.relation_ref_for_ref("raw_fixture") == RelationRef(
        catalog="warehouse_db",
        namespace="landing",
        name="fixture",
    )
    assert project.relation_ref_for_model(project.models["portable_model"]) == RelationRef(
        catalog="warehouse_db",
        namespace="staging",
        name="portable_model",
    )
    assert '"landing"."fixture"' in project.compile(project.models["portable_model"])


def test_unknown_reference_fails_during_project_load(tmp_path: Path) -> None:
    _write_model(tmp_path, "broken", "SELECT id FROM {{ ref('missing') }}")

    with pytest.raises(TransformProjectError, match="Unknown model reference: missing"):
        TransformProject.load(tmp_path, project_id="valid-project-123")


def test_cycle_fails_before_compilation(tmp_path: Path) -> None:
    _write_model(tmp_path, "first", "SELECT id FROM {{ ref('second') }}")
    _write_model(tmp_path, "second", "SELECT id FROM {{ ref('first') }}")
    project = TransformProject.load(tmp_path, project_id="valid-project-123")

    with pytest.raises(TransformProjectError, match="first -> second -> first"):
        project.ordered(["first"])


def test_model_name_must_match_sql_filename(tmp_path: Path) -> None:
    _write_model(tmp_path, "expected", "SELECT 'x' AS id")
    metadata = (tmp_path / "expected.yml").read_text()
    (tmp_path / "expected.yml").write_text(metadata.replace("model: expected", "model: other"))

    with pytest.raises(TransformProjectError, match="does not match SQL file"):
        TransformProject.load(tmp_path, project_id="valid-project-123")


def test_missing_sidecar_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "orphan.sql").write_text("SELECT 'x' AS id")

    with pytest.raises(TransformProjectError, match="Missing YAML sidecar"):
        TransformProject.load(tmp_path, project_id="valid-project-123")


def test_metadata_validation_does_not_echo_authored_value(tmp_path: Path) -> None:
    _write_model(tmp_path, "safe", "SELECT 'x' AS id")
    metadata = (tmp_path / "safe.yml").read_text()
    (tmp_path / "safe.yml").write_text(metadata.replace("owner: data-eng", "owner: ''"))

    with pytest.raises(TransformProjectError) as raised:
        TransformProject.load(tmp_path, project_id="valid-project-123")

    assert "owner" in str(raised.value)
    assert "data-eng" not in str(raised.value)


def test_model_must_compile_to_read_only_query(tmp_path: Path) -> None:
    _write_model(tmp_path, "unsafe", "DELETE FROM `project.dataset.table` WHERE TRUE")
    project = TransformProject.load(tmp_path, project_id="valid-project-123")

    with pytest.raises(TransformProjectError, match="read-only query"):
        project.compile(project.models["unsafe"])


def test_unknown_selected_model_fails(tmp_path: Path) -> None:
    _write_model(tmp_path, "known", "SELECT 'x' AS id")
    project = TransformProject.load(tmp_path, project_id="valid-project-123")

    with pytest.raises(TransformProjectError, match="Unknown selected models: absent"):
        project.ordered(["absent"])


def test_salesforce_crm_models_compile_in_governed_dependency_order() -> None:
    models = Path(__file__).parents[2] / "models"
    project = TransformProject.load(models, project_id="valid-project-123")
    selected = [
        "stg_salesforce__users",
        "stg_salesforce__accounts",
        "stg_salesforce__contacts",
        "stg_salesforce__opportunities",
        "fct_salesforce__opportunities",
    ]

    assert [model.name for model in project.ordered(selected)] == selected
    for endpoint in ("accounts", "contacts", "opportunities", "users"):
        model = project.models[f"stg_salesforce__{endpoint}"]
        assert f"`valid-project-123.raw.salesforce_{endpoint}`" in project.compile(model)

    fact = project.models["fct_salesforce__opportunities"]
    compiled = project.compile(fact)
    assert "WHERE NOT is_deleted" in compiled
    assert "owner.is_active AS owner_is_active" in compiled
    contacts = project.models["stg_salesforce__contacts"]
    relationships = {
        test.column: (test.relationships.to, test.relationships.field)
        for test in contacts.metadata.tests
        if test.relationships is not None
    }
    assert relationships == {
        "account_id": ("stg_salesforce__accounts", "account_id"),
        "owner_id": ("stg_salesforce__users", "user_id"),
    }
    assert [metric.name for metric in fact.metadata.metrics] == [
        "active_opportunity_count",
        "active_opportunity_amount",
    ]


def test_servicenow_incidents_model_loads_and_casts_internal_utc_values() -> None:
    models = Path(__file__).parents[2] / "models"
    project = TransformProject.load(models, project_id="valid-project-123")
    model = project.models["stg_servicenow__incidents"]

    assert project.ordered([model.name]) == (model,)
    compiled = project.compile(model)
    assert "`valid-project-123.raw.servicenow_incidents`" in compiled
    assert "PARSE_TIMESTAMP('%F %H:%M:%S', sys_updated_on)" in compiled
    assert [metric.name for metric in model.metadata.metrics] == ["incident_count"]
