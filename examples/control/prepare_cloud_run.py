"""Prepare a PostgreSQL Control profile for one existing v1 Cloud Run graph pipeline.

Run with ``uv run python examples/control/prepare_cloud_run.py --help``.
This writes Control metadata and local files; it does not submit or deploy a cloud job.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path

import yaml
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.control.models import PipelineGraphDocument
from dander.control.orchestration import ExecutionPlan, RetryPolicy
from dander.control.orchestration_serialization import serialize_execution_plan
from dander.control.postgresql_control_database import (
    PostgreSQLControlDatabase,
    PostgreSQLControlMigrator,
)
from dander.control.postgresql_graph_store import PostgreSQLGraphStore
from dander.control.startup_bindings import (
    PostgreSQLRunStoreStartupBinding,
    serialize_run_store_startup_binding,
)
from dander.deployment.projection import build_gcp_v1_execution_templates
from dander.project import load_project_config
from dander.providers.cloud_run import CloudRunBinding


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--gcp-project-id", required=True)
    parser.add_argument("--image", required=True, help="The deployed immutable image@sha256:digest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project", default="demo", help="Control project identifier")
    parser.add_argument("--schema", default="dander_control")
    args = parser.parse_args()
    config = args.config.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    dsn = os.environ.get("DANDER_CONTROL_DATABASE_URL")
    if not dsn:
        parser.error("Set DANDER_CONTROL_DATABASE_URL using your existing secret mechanism.")
    manifest = load_project_config(config)
    if manifest.version != 1 or manifest.launcher_provider != "cloud_run":
        parser.error("This example requires a version 1 Cloud Run project.")
    manifest.validate_references(config.parent)
    pipeline = manifest.pipelines[args.pipeline]
    if pipeline.graph is None:
        parser.error("Select a pipeline with a graph file.")
    binding = CloudRunBinding.from_project(
        config=config,
        deployment="gcp_cloud_run",
        pipeline_id=args.pipeline,
        project_id=args.gcp_project_id,
    )
    document = PipelineGraphDocument.model_validate(
        yaml.safe_load((config.parent / pipeline.graph).read_text())
    )
    template = build_gcp_v1_execution_templates(
        manifest, image=args.image, project=args.gcp_project_id
    )[args.pipeline]
    template = replace(
        template, schedule=replace(template.schedule, expression=None, time_zone=None)
    )
    startup_binding = PostgreSQLRunStoreStartupBinding(
        connection_environment_variable="DANDER_CONTROL_DATABASE_URL", schema_name=args.schema
    )
    graph_name = args.pipeline.replace("_", "-")
    with ConnectionPool(dsn, min_size=1, max_size=2, kwargs={"row_factory": dict_row}) as pool:
        pool.wait(timeout=10)
        database = PostgreSQLControlDatabase(pool=pool, schema_name=args.schema)
        PostgreSQLControlMigrator(database).migrate()
        graph = PostgreSQLGraphStore(database).create(
            args.project,
            graph_name,
            document,
            idempotency_key=f"cloud-run-example-{graph_name}",
        )
    plan = ExecutionPlan(
        plan_id=graph_name,
        environment="gcp",
        project=graph.project,
        graph=graph.graph,
        graph_revision=graph.revision,
        graph_content_sha256=graph.content_sha256,
        backend_id=template.launcher,
        profile_id=template.profile_id,
        image=template.image,
        execution_template=template,
        deadline_seconds=template.resources.deadline_seconds,
        retry_policy=RetryPolicy(max_attempts=template.resources.launcher_retry_count + 1),
    )
    plan_files = tuple((output / "plans").glob("*.json"))
    if any(path.name != f"{plan.revision}.json" for path in plan_files):
        parser.error("Use a new output directory and schema for a different immutable plan.")
    (output / "plans").mkdir(parents=True, exist_ok=True)
    (output / "plans" / f"{plan.revision}.json").write_bytes(serialize_execution_plan(plan))
    (output / "run-store.json").write_bytes(serialize_run_store_startup_binding(startup_binding))
    profile = {
        "config": str(config),
        "project": [args.project],
        "execution-plan": [f"plans/{plan.revision}.json"],
        "run-store-config": "run-store.json",
        "run-environment": "gcp",
        "gcp-project-id": args.gcp_project_id,
    }
    (output / "control.yaml").write_text(yaml.safe_dump(profile, sort_keys=False))
    print(f"Profile: {output / 'control.yaml'}")
    print(f"Graph: /v1/projects/{graph.project}/graphs/{graph.graph}")
    print(f"Existing job: {binding.job_resource}")


if __name__ == "__main__":
    main()
