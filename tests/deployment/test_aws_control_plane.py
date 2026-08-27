"""AWS hosted Control projection and verifier tests."""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

import dander.deployment.aws_control_plane as aws_control_plane
from dander.control.auth import HostedOIDCDeploymentInput
from dander.control.orchestration import ExecutionPlan, RetryPolicy, TriggerKind, TriggerSpec
from dander.control.orchestration_serialization import (
    SCHEDULED_TIME_TOKEN,
    deserialize_execution_plan,
    serialize_execution_plan,
    serialize_trigger_spec,
)
from dander.deployment.aws_control_plane import (
    AWS_CONTROL_PLANE_SCHEMA,
    AWSControlPlaneFoundationInput,
    AWSControlPlaneInput,
    preflight_aws_control_plane,
    project_aws_control_service,
    render_aws_control_foundation,
    render_aws_control_plane,
    verify_live_aws_control_plane,
    write_aws_control_plane,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.physical_plan import (
    ExchangeTransport,
    PartitioningStrategy,
    PhysicalExchange,
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalStage,
    serialize_physical_plan,
)
from dander.runtime_contract import RUNTIME_CONTRACT

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "123456789012"
REGION = "us-east-1"
DOMAIN = "d123456789abc.cloudfront.net"
ORIGIN = f"https://{DOMAIN}"
REPOSITORY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/dander"
_DANDER = REPOSITORY + "@sha256:" + "a" * 64
_DANDER_ROLLBACK = REPOSITORY + "@sha256:" + "b" * 64
_DRUFF = REPOSITORY + "@sha256:" + "c" * 64
_DRUFF_ROLLBACK = REPOSITORY + "@sha256:" + "d" * 64
GCP_PROJECT = "dander-unit-project"
GCP_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/dander/runtime@sha256:" + "f" * 64


def _platforms_config() -> str:
    return json.dumps(
        {
            "version": 1,
            "platforms": {
                "aws_native": {
                    "warehouse": {
                        "provider": "redshift",
                        "deployment": "serverless",
                        "host": "example.123456789012.us-east-1.redshift-serverless.amazonaws.com",
                        "database": "analytics",
                        "schema": "raw",
                        "region": REGION,
                        "workgroup_name": "dander-unit",
                        "database_role": "dander_runtime",
                        "copy_role_arn": f"arn:aws:iam::{ACCOUNT}:role/DanderRedshiftCopy",
                        "staging_bucket": "dander-unit-staging",
                    },
                    "state": {
                        "provider": "postgresql",
                        "authority_id": "postgresql:aws-unit",
                        "dsn_env": "DANDER_POSTGRES_DSN",
                    },
                    "catalog": {
                        "provider": "glue",
                        "region": REGION,
                        "catalog_id": ACCOUNT,
                    },
                    "secrets": {"provider": "aws_secret_manager", "region": REGION},
                },
                "gcp": {
                    "warehouse": {"provider": "bigquery", "location": "US"},
                    "state": {"provider": "bigquery"},
                    "catalog": {"provider": "dataplex"},
                    "secrets": {"provider": "gcp_secret_manager"},
                },
            },
            "deployments": {
                "aws": {
                    "platform": "aws_native",
                    "launcher": {
                        "provider": "fargate",
                        "region": REGION,
                        "aws_account_id": ACCOUNT,
                        "subnet_ids": ["subnet-0123456789abcdef0"],
                        "security_group_ids": ["sg-0123456789abcdef0"],
                        "architecture": "X86_64",
                    },
                    "runtime": {"memory": "2Gi"},
                    "safety": {"require_guarded_free_tier": False},
                    "pipelines": {"hosted_graph": {"paused": True}},
                },
                "gcp_cloud_run": {
                    "platform": "gcp",
                    "launcher": {"provider": "cloud_run", "region": "us-central1"},
                    "runtime": {"memory": "512Mi"},
                    "safety": {"require_guarded_free_tier": False},
                    "pipelines": {"hosted_graph": {"paused": True}},
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _foundation_values() -> dict[str, object]:
    return {
        "aws_account_id": ACCOUNT,
        "region": REGION,
        "name": "dander",
        "deployment_role_arn": f"arn:aws:iam::{ACCOUNT}:role/dander-bootstrap",
        "state_bucket": "dander-retained-state-unit",
        "state_prefix": "dander/d7/control-plane/aws-attempt-1/state",
        "lock_table": "dander-locks",
        "ecr_repository_url": REPOSITORY,
        "graph_bucket": "dander-d7-unit-graphs",
        "vpc_id": "vpc-12345678",
        "subnet_ids": ("subnet-22222222", "subnet-11111111"),
    }


def _foundation(**updates: object) -> AWSControlPlaneFoundationInput:
    values = _foundation_values()
    values.update(updates)
    return AWSControlPlaneFoundationInput.model_validate(values)


def _source(**updates: object) -> AWSControlPlaneInput:
    values: dict[str, object] = {
        **_foundation_values(),
        "cloudfront_distribution_id": "E123456789ABC",
        "cloudfront_domain": DOMAIN,
        "dander_image": _DANDER,
        "dander_rollback_image": _DANDER_ROLLBACK,
        "druff_image": _DRUFF,
        "druff_rollback_image": _DRUFF_ROLLBACK,
        "oidc": HostedOIDCDeploymentInput(
            api_url=ORIGIN,
            issuer="https://issuer.example.test/default",
            jwks_uri="https://issuer.example.test/default/.well-known/jwks.json",
            public_client_id="druff-aws-spa",
            api_audience="dander-aws-control",
            redirect_uri=f"{ORIGIN}/auth/callback",
            logout_uri=f"{ORIGIN}/signed-out",
            allowed_origins=(ORIGIN,),
        ),
    }
    values.update(updates)
    return AWSControlPlaneInput.model_validate(values)


def _scheduled_source(**updates: object) -> AWSControlPlaneInput:
    template = ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id="hosted_graph",
        profile_id="aws",
        launcher="fargate",
        image=_DANDER,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            "hosted_graph",
            "--platform",
            "aws",
        ),
        configuration_reference="/app/dander.yaml",
        environment=(),
        secret_bindings=(),
        workload_identity="task-role",
        resources=ResourceProjection(
            cpu_millis=1000,
            memory_mib=2048,
            ephemeral_storage_mib=21504,
            deadline_seconds=300,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=1,
            maximum_parallelism=1,
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="cloudwatch",
            metric_namespace="dander",
            alert_target=None,
            retention_days=30,
        ),
    )
    plan = ExecutionPlan(
        plan_id="aws-redshift",
        environment="production",
        project="demo",
        graph="hosted-graph",
        graph_revision="graph-r1",
        graph_content_sha256="e" * 64,
        backend_id="fargate",
        profile_id="aws",
        image=_DANDER,
        execution_template=template,
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    spec = TriggerSpec(
        trigger_id="daily-redshift",
        kind=TriggerKind.SCHEDULE,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        enabled=True,
        schedule="cron(0 6 * * ? *)",
        time_zone="America/New_York",
    )
    return _source(
        platforms_config_yaml=_platforms_config(),
        execution_plan_json=(serialize_execution_plan(plan).decode(),),
        trigger_spec_json=(serialize_trigger_spec(spec).decode(),),
        **updates,
    )


def _multicloud_source(**updates: object) -> AWSControlPlaneInput:
    scheduled = _scheduled_source()
    aws_plan = deserialize_execution_plan(scheduled.execution_plan_json[0].encode())
    gcp_template = replace(
        aws_plan.execution_template,
        profile_id="gcp",
        launcher="cloud_run",
        image=GCP_IMAGE,
        command=(*aws_plan.execution_template.command[:-1], "gcp"),
        workload_identity=(f"dander-runtime-hosted-graph@{GCP_PROJECT}.iam.gserviceaccount.com"),
        resources=replace(
            aws_plan.execution_template.resources,
            memory_mib=512,
            ephemeral_storage_mib=None,
        ),
        observability=replace(
            aws_plan.execution_template.observability,
            log_destination="cloud_logging",
            metric_namespace="run.googleapis.com",
            retention_days=None,
        ),
    )
    gcp_plan = replace(
        aws_plan,
        plan_id="gcp-bigquery",
        environment="gcp",
        backend_id="cloud_run",
        profile_id="gcp",
        image=GCP_IMAGE,
        execution_template=gcp_template,
    )
    values: dict[str, object] = {
        "platforms_config_yaml": _platforms_config(),
        "execution_plan_json": (
            scheduled.execution_plan_json[0],
            serialize_execution_plan(gcp_plan).decode(),
        ),
        "trigger_spec_json": scheduled.trigger_spec_json,
        "gcp_project_id": GCP_PROJECT,
        "gcp_control_service_account": (
            f"dander-aws-control@{GCP_PROJECT}.iam.gserviceaccount.com"
        ),
        "gcp_wif_audience": (
            "//iam.googleapis.com/projects/123456789012/locations/global/"
            "workloadIdentityPools/dander-aws-control/providers/aws-control"
        ),
    }
    values.update(updates)
    return _source(**values)


def _spark_source(**updates: object) -> AWSControlPlaneInput:
    physical = PhysicalPlan(
        pipeline_id="spark_bigquery_qualification",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        stages=(
            PhysicalStage(
                stage_id="extract",
                operators=("spark_seed",),
                partition_count=2,
            ),
            PhysicalStage(
                stage_id="publish",
                operators=("bigquery_publish",),
                partition_count=2,
                depends_on=("extract",),
            ),
        ),
        exchanges=(
            PhysicalExchange(
                exchange_id="extract-publish",
                producer_stage_id="extract",
                consumer_stage_id="publish",
                transport=ExchangeTransport.OBJECT_STORE,
                partitioning=PartitioningStrategy.ROUND_ROBIN,
                partition_count=2,
            ),
        ),
        maximum_parallelism=2,
    )
    template = ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id=physical.pipeline_id,
        profile_id="gcp",
        launcher="dataproc_serverless",
        image=GCP_IMAGE,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            physical.pipeline_id,
            "--platform",
            "gcp",
            "--project",
            GCP_PROJECT,
            "--dataset",
            "dander_spark_qualification",
            "--staging-bucket",
            "dander-unit-spark",
            "--driver-sha256",
            "9" * 64,
            "--physical-plan",
            serialize_physical_plan(physical).decode(),
        ),
        configuration_reference=("gs://dander-unit-spark/config/" + "8" * 64 + ".json"),
        environment=(),
        secret_bindings=(),
        workload_identity=f"dander-spark@{GCP_PROJECT}.iam.gserviceaccount.com",
        resources=ResourceProjection(
            cpu_millis=4_000,
            memory_mib=16_384,
            ephemeral_storage_mib=None,
            deadline_seconds=600,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=2,
            maximum_parallelism=2,
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(
            placement=(f"projects/{GCP_PROJECT}/regions/us-central1/subnetworks/dander-spark")
        ),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="cloud_logging",
            metric_namespace="dataproc.googleapis.com",
            alert_target=None,
            retention_days=None,
        ),
        extensions=(
            (
                "spark.container_image_tag",
                f"us-central1-docker.pkg.dev/{GCP_PROJECT}/dander/spark:unit-immutable",
            ),
            (
                "spark.main_python_file_uri",
                "gs://dander-unit-spark/driver/" + "9" * 64 + ".py",
            ),
            ("spark.runtime_version", "2.3"),
            ("spark.staging_bucket", "dander-unit-spark"),
        ),
    )
    plan = ExecutionPlan(
        plan_id="gcp-managed-spark",
        environment="gcp",
        project="demo",
        graph="spark-qualification",
        graph_revision="graph-r1",
        graph_content_sha256="7" * 64,
        backend_id="dataproc_serverless",
        profile_id="gcp",
        image=GCP_IMAGE,
        execution_template=template,
        deadline_seconds=600,
        retry_policy=RetryPolicy(max_attempts=2),
        physical_plan=physical,
    )
    values: dict[str, object] = {
        "platforms_config_yaml": _platforms_config(),
        "execution_plan_json": (serialize_execution_plan(plan).decode(),),
        "run_environment": "gcp",
        "gcp_project_id": GCP_PROJECT,
        "gcp_control_service_account": (
            f"dander-aws-control@{GCP_PROJECT}.iam.gserviceaccount.com"
        ),
        "gcp_wif_audience": (
            "//iam.googleapis.com/projects/123456789012/locations/global/"
            "workloadIdentityPools/dander-aws-control/providers/aws-control"
        ),
    }
    values.update(updates)
    return _source(**values)


def test_inputs_are_closed_immutable_and_use_exact_aws_boundaries() -> None:
    source = _source()
    assert source.subnet_ids == ("subnet-11111111", "subnet-22222222")
    with pytest.raises(ValidationError, match="frozen"):
        source.region = "us-west-2"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="fixed D7 prefix"):
        _foundation(state_prefix="dander/state")
    with pytest.raises(ValidationError, match="reviewed D7 name prefix"):
        _foundation(graph_bucket="unrelated-unit-graphs")
    with pytest.raises(ValidationError, match="selected ECR repository"):
        _source(dander_image=f"{ACCOUNT}.dkr.ecr.us-west-2.amazonaws.com/dander@sha256:" + "a" * 64)
    with pytest.raises(ValidationError, match="selected ECR repository"):
        _source(dander_rollback_image=REPOSITORY + "/other@sha256:" + "b" * 64)
    with pytest.raises(ValidationError, match="exact CloudFront origin"):
        _source(
            oidc=HostedOIDCDeploymentInput(
                **{
                    **source.oidc.model_dump(),
                    "api_url": "https://other.example.test",
                    "redirect_uri": "https://other.example.test/auth/callback",
                    "logout_uri": "https://other.example.test/signed-out",
                    "allowed_origins": ("https://other.example.test",),
                }
            )
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        AWSControlPlaneFoundationInput.model_validate({**_foundation_values(), "extensions": {}})


def test_foundation_and_complete_projection_are_deterministic_and_provider_neutral() -> None:
    foundation = _foundation()
    source = _source()
    foundation_rendered = render_aws_control_foundation(foundation)
    rendered = render_aws_control_plane(source)
    service = project_aws_control_service(source)

    assert foundation_rendered == render_aws_control_foundation(foundation)
    assert set(foundation_rendered) == {"deployment.json", "foundation.tfvars.json"}
    assert json.loads(foundation_rendered["foundation.tfvars.json"])["foundation_only"] is True
    assert rendered == render_aws_control_plane(source)
    assert set(rendered) == {
        "Caddyfile",
        "active.tfvars.json",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "foundation.tfvars.json",
        "public-client.json",
        "rollback.tfvars.json",
    }
    assert service.profile_id == "aws_fargate"
    assert service.workload_identity == f"arn:aws:iam::{ACCOUNT}:role/dander-d7-control-task"
    assert service.command[-4:] == (
        "--oidc-config",
        "/etc/dander/oidc/control-oidc.json",
        "--graph-store-config",
        "/etc/dander/graph-store/control-graph-store.json",
    )
    assert json.loads(rendered["control-graph-store.json"]) == {
        "kind": "s3",
        "bucket": "dander-d7-unit-graphs",
        "prefix": "dander-control/v1",
        "expected_bucket_owner": ACCOUNT,
    }
    active = json.loads(rendered["active.tfvars.json"])
    rollback = json.loads(rendered["rollback.tfvars.json"])
    manifest = json.loads(rendered["deployment.json"])
    assert active["dander_image"] == _DANDER
    assert rollback["dander_image"] == _DANDER_ROLLBACK
    assert active["control_args"] == list(service.command)
    assert manifest["schema"] == AWS_CONTROL_PLANE_SCHEMA
    assert manifest["browser_origin"] == ORIGIN
    assert manifest["cloudfront"] == {
        "access_logs": False,
        "api_cache_ttl_seconds": 0,
        "api_cookies": "none",
        "api_headers": "allViewer",
        "api_query_strings": "all",
        "static_minimum_ttl_seconds": 0,
    }
    assert "root * /app" in rendered["Caddyfile"]
    assert "log" not in {line.strip() for line in rendered["Caddyfile"].splitlines()}
    combined = "".join(rendered.values()).casefold()
    assert "client_secret" not in combined
    assert "refresh_token" not in combined
    assert "private_key" not in combined


def test_schedule_projection_routes_exact_occurrences_through_control() -> None:
    source = _scheduled_source()
    rendered = render_aws_control_plane(source)
    active = json.loads(rendered["active.tfvars.json"])
    manifest = json.loads(rendered["deployment.json"])
    plan = source.execution_plans[0]
    spec = source.trigger_specs[0]

    assert active["execution_plan_json"] == {plan.revision: source.execution_plan_json[0]}
    assert active["platforms_config_yaml"] == source.platforms_config_yaml
    assert active["trigger_spec_json"] == {spec.trigger_id: source.trigger_spec_json[0]}
    assert active["control_schedules"][spec.trigger_id] == {
        "enabled": True,
        "expression": "cron(0 6 * * ? *)",
        "message": (
            '{"plan_revision":"'
            + plan.revision
            + '","scheduled_occurrence":"'
            + SCHEDULED_TIME_TOKEN
            + '","schema":"io.dander.control.schedule-wakeup/v1",'
            '"trigger_id":"daily-redshift"}'
        ),
        "plan_revision": plan.revision,
        "time_zone": "America/New_York",
    }
    assert active["control_args"][-6:] == [
        "--run-store-prefix",
        "dander-control/v1",
        "--run-environment",
        "production",
        "--trigger-spec",
        "/etc/dander/orchestration/triggers/daily-redshift.json",
    ]
    assert active["control_args"][len(project_aws_control_service(source).command) :][:2] == [
        "--platforms-config",
        "/etc/dander/dander.platforms.yaml",
    ]
    assert active["control_args"][len(project_aws_control_service(source).command) + 2 :][:4] == [
        "--project",
        "demo",
        "--aws-deployment-name",
        "dander",
    ]
    resource_name = "dander-hosted-graph-fad90246"
    assert active["control_fargate_bindings"] == {
        plan.revision: {
            "execution_arn_prefix": (
                f"arn:aws:states:{REGION}:{ACCOUNT}:execution:{resource_name}:"
            ),
            "log_group_arn": (
                f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/dander/dander/hosted_graph:*"
            ),
            "state_machine_arn": (
                f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{resource_name}"
            ),
        }
    }
    assert manifest["orchestration"] == {
        "execution_plan_revisions": [plan.revision],
        "fargate_deployment_name": "dander",
        "run_environment": "production",
        "run_store_bucket": "dander-d7-unit-graphs",
        "run_store_prefix": "dander-control/v1",
        "scheduled_trigger_ids": ["daily-redshift"],
    }
    terraform = (ROOT / "infra" / "aws-control" / "main.tf").read_text(encoding="utf-8")
    scheduler_trust = terraform.split('data "aws_iam_policy_document" "scheduler_assume"', 1)[
        1
    ].split('resource "aws_iam_role" "scheduler"', 1)[0]
    assert 'variable = "aws:SourceAccount"' in scheduler_trust
    assert "schedule-group/default" in scheduler_trust
    fargate_policy = terraform.split('data "aws_iam_policy_document" "control_fargate"', 1)[
        1
    ].split('resource "aws_iam_role_policy" "control_fargate"', 1)[0]
    assert 'actions   = ["states:StartExecution"]' in fargate_policy
    assert '"states:DescribeExecution"' in fargate_policy
    assert '"states:GetExecutionHistory"' in fargate_policy
    assert '"states:StopExecution"' in fargate_policy
    assert 'actions   = ["logs:FilterLogEvents"]' in fargate_policy
    assert 'actions   = ["ecs:DescribeTasks"]' in fargate_policy

    with pytest.raises(ValidationError, match="canonical JSON"):
        _source(execution_plan_json=("{}",))
    with pytest.raises(ValidationError, match="require platform configuration"):
        _source(execution_plan_json=source.execution_plan_json)
    with pytest.raises(ValidationError, match="valid YAML"):
        _source(platforms_config_yaml="version: [")
    mismatched_platforms = json.loads(source.platforms_config_yaml)
    mismatched_platforms["deployments"]["aws"]["launcher"]["region"] = "us-west-2"
    with pytest.raises(ValidationError, match="matching Fargate platform bindings"):
        _source(
            platforms_config_yaml=json.dumps(mismatched_platforms),
            execution_plan_json=source.execution_plan_json,
        )
    with pytest.raises(ValidationError, match="exact configured plan"):
        _source(
            platforms_config_yaml=source.platforms_config_yaml,
            execution_plan_json=source.execution_plan_json,
            trigger_spec_json=(
                serialize_trigger_spec(
                    TriggerSpec(
                        trigger_id="wrong-plan",
                        kind=TriggerKind.SCHEDULE,
                        plan_id="other-plan",
                        plan_revision=plan.revision,
                        enabled=True,
                        schedule="rate(1 day)",
                        time_zone="UTC",
                    )
                ).decode(),
            ),
        )


def test_automatic_placement_renders_one_bounded_control_startup_policy() -> None:
    base = _multicloud_source()
    plans = base.execution_plans
    candidates = tuple(
        f"{plan.revision},{'us-east-1' if plan.backend_id == 'fargate' else 'us-central1'},"
        f"{400 if plan.backend_id == 'fargate' else 100}"
        for plan in reversed(plans)
    )
    source = _multicloud_source(
        run_environment="auto",
        run_placement_candidates=candidates,
        run_preferred_locality="us-east-1",
        run_max_cost_microusd=500,
        run_size_candidates=tuple(f"{plan.revision},small,1000" for plan in plans),
        run_default_size_class="small",
    )

    rendered = render_aws_control_plane(source)
    active = json.loads(rendered["active.tfvars.json"])
    manifest = json.loads(rendered["deployment.json"])
    args = active["control_args"]

    assert source.run_placement_candidates == tuple(sorted(candidates))
    assert args.count("--run-placement-candidate") == 2
    assert args[args.index("--run-environment") + 1] == "auto"
    assert args[args.index("--run-preferred-locality") + 1] == "us-east-1"
    assert args[args.index("--run-max-cost-microusd") + 1] == "500"
    assert manifest["orchestration"]["run_placement_candidates"] == list(
        source.run_placement_candidates
    )
    assert args.count("--run-size-candidate") == 2
    assert args[args.index("--run-default-size-class") + 1] == "small"
    assert manifest["orchestration"]["run_size_candidates"] == list(source.run_size_candidates)

    with pytest.raises(ValidationError, match="requires candidates, locality, and max cost"):
        _multicloud_source(run_environment="auto")
    with pytest.raises(ValidationError, match="unconfigured plan"):
        _multicloud_source(
            run_environment="auto",
            run_placement_candidates=(f"{'0' * 64},us-east-1,1",),
            run_preferred_locality="us-east-1",
            run_max_cost_microusd=500,
        )


def test_multicloud_projection_keeps_one_control_and_scopes_each_backend() -> None:
    source = _multicloud_source()
    rendered = render_aws_control_plane(source)
    active = json.loads(rendered["active.tfvars.json"])
    plans = {plan.backend_id: plan for plan in source.execution_plans}

    assert set(active["execution_plan_json"]) == {
        plans["fargate"].revision,
        plans["cloud_run"].revision,
    }
    assert set(active["control_fargate_bindings"]) == {plans["fargate"].revision}
    assert active["control_cloud_run_plan_revisions"] == [plans["cloud_run"].revision]
    assert active["gcp_control_service_account"] == source.gcp_control_service_account
    assert active["gcp_wif_audience"] == source.gcp_wif_audience
    assert active["control_args"].count("control") == 1
    assert active["control_args"].count("serve") == 1
    gcp_index = active["control_args"].index("--gcp-project-id")
    assert active["control_args"][gcp_index : gcp_index + 4] == [
        "--gcp-project-id",
        GCP_PROJECT,
        "--gcp-deployment-name",
        "gcp_cloud_run",
    ]
    with pytest.raises(ValidationError, match="workload identity inputs"):
        _multicloud_source(gcp_wif_audience="")


def test_managed_spark_projection_uses_the_existing_gcp_identity_handoff() -> None:
    source = _spark_source()
    rendered = render_aws_control_plane(source)
    active = json.loads(rendered["active.tfvars.json"])
    plan = source.execution_plans[0]

    assert active["control_fargate_bindings"] == {}
    assert active["control_cloud_run_plan_revisions"] == []
    assert active["control_dataproc_plan_revisions"] == [plan.revision]
    assert active["gcp_control_service_account"] == source.gcp_control_service_account
    assert active["gcp_wif_audience"] == source.gcp_wif_audience
    gcp_index = active["control_args"].index("--gcp-project-id")
    assert active["control_args"][gcp_index : gcp_index + 2] == [
        "--gcp-project-id",
        GCP_PROJECT,
    ]
    assert "--gcp-deployment-name" not in active["control_args"]
    manifest = json.loads(rendered["deployment.json"])
    assert manifest["orchestration"]["gcp_project_id"] == GCP_PROJECT
    assert "gcp_deployment_name" not in manifest["orchestration"]
    with pytest.raises(ValidationError, match="GCP execution plan"):
        _scheduled_source(
            gcp_project_id=GCP_PROJECT,
            gcp_control_service_account=source.gcp_control_service_account,
            gcp_wif_audience=source.gcp_wif_audience,
        )


@pytest.mark.parametrize("complete", [False, True])
def test_write_and_preflight_are_mode_bounded_and_backend_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete: bool,
) -> None:
    source: AWSControlPlaneFoundationInput | AWSControlPlaneInput = (
        _source() if complete else _foundation()
    )
    output = tmp_path / "aws"
    commands: list[tuple[str, ...]] = []

    def fake_run(
        command: tuple[str, ...], *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert environment is not None
        assert environment["TF_DATA_DIR"] == str(output / "terraform-data")
        assert (
            "-backend=false" in command
            or command[-2:] == ("fmt", "-check")
            or command[-1] == "validate"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(aws_control_plane, "_run", fake_run)
    written = write_aws_control_plane(source, output_directory=output)
    result = preflight_aws_control_plane(
        source,
        output_directory=output,
        terraform_root=ROOT / "infra" / "aws-control",
    )

    assert result["status"] == "passed"
    assert result["stage"] == ("complete" if complete else "foundation")
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in written)
    assert len(commands) == 3


@pytest.mark.parametrize("environment", ["active", "rollback"])
def test_live_verifier_is_read_only_and_checks_exact_provider_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["active", "rollback"],
) -> None:
    source = _scheduled_source()
    rendered = render_aws_control_plane(source)
    active = json.loads(rendered["active.tfvars.json"])
    desired = (
        {"control": _DANDER, "druff": _DRUFF}
        if environment == "active"
        else {"control": _DANDER_ROLLBACK, "druff": _DRUFF_ROLLBACK}
    )
    calls: list[tuple[str, ...]] = []

    def fake_aws(
        _source_value: AWSControlPlaneFoundationInput,
        *arguments: str,
        regional: bool = True,
    ) -> object:
        calls.append(arguments)
        if arguments[:2] == ("sts", "get-caller-identity"):
            return {"Account": ACCOUNT}
        if arguments[:2] == ("cloudfront", "get-distribution"):
            return _distribution()
        if arguments[:2] == ("cloudfront", "get-cache-policy"):
            policy_id = arguments[-1]
            return {
                "CachePolicy": {
                    "CachePolicyConfig": {
                        "DefaultTTL": 0,
                        "MaxTTL": 0 if policy_id == "api-cache" else 31536000,
                        "MinTTL": 0,
                    }
                }
            }
        if arguments[:2] == ("cloudfront", "get-origin-request-policy"):
            return {
                "OriginRequestPolicy": {
                    "OriginRequestPolicyConfig": {
                        "HeadersConfig": {"HeaderBehavior": "allViewer"},
                        "CookiesConfig": {"CookieBehavior": "none"},
                        "QueryStringsConfig": {"QueryStringBehavior": "all"},
                    }
                }
            }
        if arguments[:2] == ("elbv2", "describe-load-balancers"):
            return {
                "LoadBalancers": [
                    {
                        "LoadBalancerArn": (
                            f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
                            "loadbalancer/app/dander-d7-control/unit"
                        ),
                        "DNSName": "dander-d7-control.us-east-1.elb.amazonaws.com",
                        "Scheme": "internet-facing",
                        "Type": "application",
                        "State": {"Code": "active"},
                    }
                ]
            }
        if arguments[:2] == ("elbv2", "describe-load-balancer-attributes"):
            return {
                "Attributes": [
                    {"Key": "access_logs.s3.enabled", "Value": "false"},
                    {"Key": "deletion_protection.enabled", "Value": "false"},
                ]
            }
        if arguments[:2] == ("ecs", "describe-services"):
            return {
                "failures": [],
                "services": [
                    _service("dander-d7-control"),
                    _service("dander-d7-druff"),
                ],
            }
        if arguments[:2] == ("ecs", "list-tasks"):
            service = arguments[arguments.index("--service-name") + 1]
            return {"taskArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/unit/{service}"]}
        if arguments[:2] == ("ecs", "describe-tasks"):
            task = arguments[-1]
            task_workload: Literal["control", "druff"] = (
                "control" if task.endswith("control") else "druff"
            )
            return {
                "tasks": [
                    {
                        "lastStatus": "RUNNING",
                        "healthStatus": "HEALTHY",
                        "taskDefinitionArn": (
                            f"arn:aws:ecs:{REGION}:{ACCOUNT}:"
                            f"task-definition/dander-d7-{task_workload}:1"
                        ),
                        "containers": [{"name": task_workload, "image": desired[task_workload]}],
                    }
                ]
            }
        if arguments[:2] == ("ecs", "describe-task-definition"):
            task_definition = arguments[arguments.index("--task-definition") + 1]
            definition_workload: Literal["control", "druff"] = (
                "control" if "-control:" in task_definition else "druff"
            )
            return _task_definition(source, rendered, desired, definition_workload)
        if arguments[:2] == ("elbv2", "describe-target-health"):
            return {"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]}
        if arguments[:2] == ("s3api", "get-bucket-versioning"):
            assert regional is False
            return {"Status": "Enabled"}
        if arguments[:2] == ("s3api", "get-public-access-block"):
            return {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                }
            }
        if arguments[:2] == ("s3api", "get-bucket-encryption"):
            return {"ServerSideEncryptionConfiguration": {"Rules": [{}]}}
        if arguments[:2] == ("sqs", "get-queue-attributes"):
            queue_url = arguments[arguments.index("--queue-url") + 1]
            if queue_url.endswith("-dlq"):
                return {
                    "Attributes": {
                        "QueueArn": (
                            f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedule-dlq"
                        ),
                        "SqsManagedSseEnabled": "true",
                    }
                }
            return {
                "Attributes": {
                    "QueueArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedules",
                    "SqsManagedSseEnabled": "true",
                    "ReceiveMessageWaitTimeSeconds": "20",
                    "VisibilityTimeout": "120",
                    "RedrivePolicy": json.dumps(
                        {
                            "deadLetterTargetArn": (
                                f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedule-dlq"
                            ),
                            "maxReceiveCount": 5,
                        }
                    ),
                }
            }
        if arguments[:2] == ("scheduler", "get-schedule"):
            spec = source.trigger_specs[0]
            return {
                "State": "ENABLED",
                "ScheduleExpression": spec.schedule,
                "ScheduleExpressionTimezone": spec.time_zone,
                "FlexibleTimeWindow": {"Mode": "OFF"},
                "Target": {
                    "Arn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedules",
                    "RoleArn": f"arn:aws:iam::{ACCOUNT}:role/dander-d7-scheduler",
                    "Input": active["control_schedules"][spec.trigger_id]["message"],
                    "DeadLetterConfig": {
                        "Arn": (f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedule-dlq")
                    },
                    "RetryPolicy": {
                        "MaximumEventAgeInSeconds": 3600,
                        "MaximumRetryAttempts": 3,
                    },
                },
            }
        raise AssertionError(arguments)

    monkeypatch.setattr(aws_control_plane, "_aws_json", fake_aws)
    monkeypatch.setattr(
        aws_control_plane,
        "_http_json",
        lambda url: {"status": "ready"} if url.endswith("readyz") else {"status": "ok"},
    )
    monkeypatch.setattr(
        aws_control_plane,
        "_http_bytes",
        lambda _url: (
            rendered["bootstrap.json"].encode(),
            {"cache-control": "no-store"},
        ),
    )

    result = verify_live_aws_control_plane(source, environment=environment)

    assert result["status"] == "passed"
    assert result["images"] == desired
    assert result["scheduling"] == {
        "queue_arn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedules",
        "dead_letter_arn": (f"arn:aws:sqs:{REGION}:{ACCOUNT}:dander-d7-control-schedule-dlq"),
        "scheduled_trigger_ids": ["daily-redshift"],
    }
    assert calls
    assert all(
        arguments[1].startswith(("get-", "describe-", "list-"))
        or arguments[:2] == ("sts", "get-caller-identity")
        for arguments in calls
    )


def _distribution() -> dict[str, object]:
    return {
        "Distribution": {
            "Status": "Deployed",
            "DomainName": DOMAIN,
            "DistributionConfig": {
                "Enabled": True,
                "Logging": {"Enabled": False},
                "Origins": {
                    "Items": [{"DomainName": "dander-d7-control.us-east-1.elb.amazonaws.com"}]
                },
                "DefaultCacheBehavior": {"CachePolicyId": "static-cache"},
                "CacheBehaviors": {
                    "Items": [
                        {
                            "PathPattern": "/v1/*",
                            "CachePolicyId": "api-cache",
                            "OriginRequestPolicyId": "api-origin",
                            "AllowedMethods": {
                                "Items": [
                                    "DELETE",
                                    "GET",
                                    "HEAD",
                                    "OPTIONS",
                                    "PATCH",
                                    "POST",
                                    "PUT",
                                ]
                            },
                        },
                        {
                            "PathPattern": "/healthz",
                            "CachePolicyId": "api-cache",
                            "OriginRequestPolicyId": "api-origin",
                        },
                        {
                            "PathPattern": "/readyz",
                            "CachePolicyId": "api-cache",
                            "OriginRequestPolicyId": "api-origin",
                        },
                    ]
                },
            },
        }
    }


def _service(name: str) -> dict[str, object]:
    workload = "control" if name.endswith("-control") else "druff"
    return {
        "serviceName": name,
        "status": "ACTIVE",
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "deployments": [{"status": "PRIMARY"}],
        "loadBalancers": [
            {
                "targetGroupArn": (
                    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
                    f"targetgroup/dander-d7-{workload}/unit"
                )
            }
        ],
    }


def _task_definition(
    source: AWSControlPlaneInput,
    rendered: dict[str, str],
    images: dict[str, str],
    workload: Literal["control", "druff"],
) -> dict[str, object]:
    if workload == "control":
        active = json.loads(rendered["active.tfvars.json"])
        environment = {
            "CONTROL_OIDC_B64": _b64(rendered["control-oidc.json"]),
            "GRAPH_STORE_B64": _b64(rendered["control-graph-store.json"]),
            "PLATFORMS_CONFIG_B64": _b64(active["platforms_config_yaml"]),
            "EXECUTION_PLANS_B64": _b64(
                json.dumps(active["execution_plan_json"], sort_keys=True, separators=(",", ":"))
            ),
            "TRIGGER_SPECS_B64": _b64(
                json.dumps(active["trigger_spec_json"], sort_keys=True, separators=(",", ":"))
            ),
        }
        role = source.control_task_role_arn
    else:
        environment = {
            "DRUFF_BOOTSTRAP_B64": _b64(rendered["bootstrap.json"]),
            "DRUFF_CADDY_B64": _b64(rendered["Caddyfile"]),
        }
        role = source.control_task_role_arn.replace("-control-task", "-druff-task")
    return {
        "taskDefinition": {
            "status": "ACTIVE",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "taskRoleArn": role,
            "containerDefinitions": [
                {
                    "name": "config-init",
                    "image": images["control"],
                    "essential": False,
                    "readonlyRootFilesystem": True,
                    "user": "0:0",
                    "linuxParameters": {"capabilities": {"add": [], "drop": ["ALL"]}},
                    "environment": [
                        {"name": name, "value": value} for name, value in environment.items()
                    ],
                },
                {
                    "name": workload,
                    "image": images[workload],
                    "essential": True,
                    "readonlyRootFilesystem": True,
                    "user": "65532:65532",
                    "linuxParameters": {"capabilities": {"add": [], "drop": ["ALL"]}},
                    "dependsOn": [{"containerName": "config-init", "condition": "SUCCESS"}],
                    "mountPoints": [
                        {"containerPath": "/etc/dander", "readOnly": True},
                        {"containerPath": "/tmp", "readOnly": False},
                    ],
                    **(
                        {
                            "command": [
                                *active["control_args"],
                                "--schedule-queue-url",
                                (
                                    f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/"
                                    "dander-d7-control-schedules"
                                ),
                            ]
                        }
                        if workload == "control"
                        else {}
                    ),
                },
            ],
        }
    }


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()
