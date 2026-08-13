"""Cross-layer characterization of the released GCP compatibility profile."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _normalized(path: Path) -> str:
    return "\n".join(
        " ".join(line.split()) for line in path.read_text(encoding="utf-8").splitlines()
    )


def test_bigquery_publication_keeps_transactional_lease_fencing() -> None:
    writer = (ROOT / "src/dander/writer/bigquery.py").read_text(encoding="utf-8")
    fencing = (ROOT / "src/dander/concurrency.py").read_text(encoding="utf-8")

    assert '"BEGIN TRANSACTION;\\n"' in writer
    assert "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in fencing
    assert 'f"MERGE `{target_id}` AS target\\n"' in writer
    assert "_STAGING_TTL = timedelta(days=1)" in writer


def test_bigquery_state_keeps_lease_and_watermark_compare_and_set() -> None:
    lease = (ROOT / "src/dander/state/lease.py").read_text(encoding="utf-8")
    watermark = (ROOT / "src/dander/state/watermark.py").read_text(encoding="utf-8")

    assert "fencing_token = fencing_token + 1" in lease
    assert "fencing_token = @fencing_token" in lease
    assert "@cursor AS last_cursor" in watermark
    assert "target.last_cursor IS NOT DISTINCT FROM @expected" in watermark
    assert "ASSERT @@row_count = 1 AS 'Dander watermark boundary changed'" in watermark


def test_cloud_run_projection_keeps_existing_version_one_behavior() -> None:
    module = _normalized(ROOT / "infra/modules/scheduled-job/main.tf")
    variables = _normalized(ROOT / "infra/modules/scheduled-job/variables.tf")

    assert 'resource "google_cloud_run_v2_job" "ingestion" {' in module
    assert "task_count = var.execution_projections[each.key].schedule.task_count" in module
    assert (
        "parallelism = var.execution_projections[each.key].schedule.maximum_parallelism" in module
    )
    assert "image = var.execution_projections[each.key].image" in module
    assert "args = var.execution_projections[each.key].command" in module
    assert "launcher_retry_count" in module
    assert "runtime_retry_count == 0" in variables


def test_cli_and_distribution_keep_the_public_gcp_compatibility_surface() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    packaged = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert project["name"] == "dander-platform"
    assert project["scripts"]["dander"] == "dander.cli.entrypoint:main"
    assert "infra/main.tf" in packaged
    assert "infra/modules/scheduled-job/main.tf" in packaged
