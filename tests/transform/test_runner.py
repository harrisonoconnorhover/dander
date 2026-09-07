"""BigQuery transform materialization and assertion tests."""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from dander.concurrency import FencingToken
from dander.telemetry import TelemetryOperation
from dander.transform import BigQueryTransformRunner, TransformProjectError, TransformRunError

if TYPE_CHECKING:
    from pathlib import Path

    from google.cloud import bigquery


class _FakeJob:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows
        self.result_calls = 0
        self.total_bytes_processed = 0

    def result(self) -> list[_FakeRow]:
        self.result_calls += 1
        assert self.result_calls == 1
        self.total_bytes_processed = 64
        return self._rows


class _FakeRow:
    """BigQuery-like row: keyed lookup works, membership checks inspect values."""

    def __init__(self, **values: object) -> None:
        self._values = values

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __contains__(self, value: object) -> bool:
        return value in self._values.values()


class _FakeClient:
    def __init__(self, assertion_failures: list[int] | None = None) -> None:
        self.queries: list[str] = []
        self.configs: list[bigquery.QueryJobConfig | None] = []
        self.assertion_failures = list(assertion_failures or [])

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FakeJob:
        self.queries.append(query)
        self.configs.append(job_config)
        if " AS failures" in query:
            failures = self.assertion_failures.pop(0) if self.assertion_failures else 0
            return _FakeJob([_FakeRow(failures=failures)])
        return _FakeJob([])


class _Ownership:
    def __init__(self, fence: FencingToken) -> None:
        self.fence = fence
        self.verifications = 0

    def verify(self) -> None:
        self.verifications += 1


def _write_model(
    root: Path,
    name: str,
    *,
    materialization: str = "view",
    ref: str = "raw_fixture",
    tests: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.sql").write_text(
        f"SELECT CAST(id AS STRING) AS id FROM {{{{ ref('{ref}') }}}}"
    )
    incremental = (
        "\n            unique_key: [id]\n            incremental_cursor: id"
        if materialization == "incremental"
        else ""
    )
    (root / f"{name}.yml").write_text(
        dedent(
            f"""
            model: {name}
            description: Safe test model.
            owner: data-eng
            materialization: {materialization}
            dataset: staging
            source_system: fixture
            sensitivity: public
            {incremental}
            columns:
              - name: id
                type: STRING
                description: Fixture identifier.
            tests:
            {tests}
            """
        )
    )


def test_build_materializes_then_runs_all_generic_assertions(tmp_path: Path) -> None:
    tests = """
              - column: id
                not_null: true
                unique: true
                accepted_values: [open, closed]
                relationships:
                  to: raw_parent
                  field: id
    """
    _write_model(tmp_path, "model_a", materialization="table", tests=tests)
    client = _FakeClient()

    result = BigQueryTransformRunner(project="valid-project-123", client=client).build(
        tmp_path,
        selected=["model_a"],
    )

    assert result.models == ("model_a",)
    assert result.assertions == 4
    assert [operation.operation for operation in result.telemetry] == [
        TelemetryOperation.TRANSFORM,
        *([TelemetryOperation.TEST] * 4),
    ]
    assert sum(operation.bytes_processed for operation in result.telemetry) == 5 * 64
    assert client.queries[0].startswith(
        "CREATE OR REPLACE TABLE `valid-project-123.staging.model_a` AS"
    )
    assert "COUNTIF(`id` IS NULL)" in client.queries[1]
    assert "HAVING COUNT(*) > 1" in client.queries[2]
    assert "'open', 'closed'" in client.queries[3]
    assert "LEFT JOIN `valid-project-123.raw.parent` AS parent" in client.queries[4]


def test_test_command_path_does_not_materialize(tmp_path: Path) -> None:
    tests = """
              - column: id
                not_null: true
    """
    _write_model(tmp_path, "model_a", tests=tests)
    client = _FakeClient()

    result = BigQueryTransformRunner(project="valid-project-123", client=client).test(tmp_path)

    assert result.assertions == 1
    assert [operation.operation for operation in result.telemetry] == [TelemetryOperation.TEST]
    assert all(not query.startswith("CREATE") for query in client.queries)


def test_assertion_failures_name_tests_without_row_values(tmp_path: Path) -> None:
    tests = """
              - column: id
                not_null: true
                unique: true
    """
    _write_model(tmp_path, "model_a", tests=tests)
    client = _FakeClient(assertion_failures=[2, 0])

    with pytest.raises(TransformRunError, match=r"model_a\.id\.not_null"):
        BigQueryTransformRunner(project="valid-project-123", client=client).build(tmp_path)


def test_incremental_materialization_builds_watermark_bounded_merge(tmp_path: Path) -> None:
    _write_model(tmp_path, "base", tests="              []")
    _write_model(
        tmp_path,
        "model_a",
        materialization="incremental",
        ref="base",
        tests="              []",
    )
    (tmp_path / "model_a.sql").write_text(
        "SELECT CAST(id AS STRING) AS id, 'active' AS status FROM {{ ref('base') }}"
    )
    sidecar = tmp_path / "model_a.yml"
    sidecar.write_text(
        sidecar.read_text().replace(
            "description: Fixture identifier.",
            "description: Fixture identifier.\n"
            "  - name: status\n"
            "    type: STRING\n"
            "    description: Fixture status.",
        )
    )
    client = _FakeClient()

    result = BigQueryTransformRunner(project="valid-project-123", client=client).build(
        tmp_path,
        selected=["model_a"],
    )

    assert result.models == ("base", "model_a")
    create = client.queries[1]
    incremental = client.queries[2]
    assert create.startswith("CREATE TABLE IF NOT EXISTS `valid-project-123.staging.model_a`")
    assert incremental.startswith("MERGE `valid-project-123.staging.model_a` AS target")
    assert "source.`id` >= (SELECT MAX(`id`)" in incremental
    assert "PARTITION BY source.`id`" in incremental
    assert "ORDER BY source.`id` DESC, TO_JSON_STRING(source) DESC" in incremental
    assert "WHEN MATCHED THEN UPDATE SET target.`status` = source.`status`" in incremental
    assert (
        "WHEN NOT MATCHED THEN INSERT (`id`, `status`) "
        "VALUES (source.`id`, source.`status`)" in incremental
    )
    assert "SELECT *" not in create + incremental


def test_incremental_merge_is_fenced_in_same_transaction(tmp_path: Path) -> None:
    _write_model(
        tmp_path,
        "model_a",
        materialization="incremental",
        tests="              []",
    )
    client = _FakeClient()
    ownership = _Ownership(
        FencingToken(
            lease_table="unit-project.meta._dander_leases",
            pipeline_id="example_pipeline",
            run_id="run-one",
            token=5,
        )
    )

    BigQueryTransformRunner(project="unit-project", client=client).build(
        tmp_path,
        ownership=ownership,
    )

    assert client.queries[0].startswith("CREATE TABLE IF NOT EXISTS")
    script = client.queries[1]
    assert script.startswith("BEGIN TRANSACTION;\nUPDATE `unit-project.meta._dander_leases`")
    assert "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in script
    assert "MERGE `unit-project.staging.model_a`" in script
    assert script.index("UPDATE `unit-project.meta._dander_leases`") < script.index(
        "MERGE `unit-project.staging.model_a`"
    )
    assert "SELECT" not in script.split("ASSERT @@row_count", 1)[0]
    assert ownership.verifications == 2


@pytest.mark.parametrize("missing_line", ["unique_key: [id]", "incremental_cursor: id"])
def test_incremental_metadata_requires_key_and_cursor_before_queries(
    tmp_path: Path,
    missing_line: str,
) -> None:
    _write_model(
        tmp_path,
        "model_a",
        materialization="incremental",
        tests="              []",
    )
    sidecar = tmp_path / "model_a.yml"
    sidecar.write_text(sidecar.read_text().replace(missing_line, ""))
    client = _FakeClient()

    with pytest.raises(TransformProjectError, match="Invalid model metadata"):
        BigQueryTransformRunner(project="valid-project-123", client=client).build(tmp_path)

    assert client.queries == []


def test_selected_build_includes_model_dependencies(tmp_path: Path) -> None:
    _write_model(tmp_path, "base", tests="              []")
    _write_model(tmp_path, "consumer", ref="base", tests="              []")
    client = _FakeClient()

    result = BigQueryTransformRunner(project="valid-project-123", client=client).build(
        tmp_path,
        selected=["consumer"],
    )

    assert result.models == ("base", "consumer")
    assert client.queries[0].startswith("CREATE OR REPLACE VIEW")
    assert "`valid-project-123.staging.base`" in client.queries[1]


def test_relationship_target_field_fails_before_queries(tmp_path: Path) -> None:
    _write_model(tmp_path, "parent", tests="              []")
    tests = """
              - column: id
                relationships:
                  to: parent
                  field: missing
    """
    _write_model(tmp_path, "child", ref="parent", tests=tests)
    client = _FakeClient()

    with pytest.raises(TransformProjectError, match="parent.missing"):
        BigQueryTransformRunner(project="valid-project-123", client=client).build(
            tmp_path,
            selected=["child"],
        )

    assert client.queries == []
