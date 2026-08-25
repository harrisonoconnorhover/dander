"""AWS hosted Control projection and verifier tests."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

import dander.deployment.aws_control_plane as aws_control_plane
from dander.control.auth import HostedOIDCDeploymentInput
from dander.control.orchestration import ExecutionPlan, RetryPolicy, TriggerKind, TriggerSpec
from dander.control.orchestration_serialization import (
    SCHEDULED_TIME_TOKEN,
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
        execution_plan_json=(serialize_execution_plan(plan).decode(),),
        trigger_spec_json=(serialize_trigger_spec(spec).decode(),),
        **updates,
    )


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
    assert manifest["orchestration"] == {
        "execution_plan_revisions": [plan.revision],
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

    with pytest.raises(ValidationError, match="canonical JSON"):
        _source(execution_plan_json=("{}",))
    with pytest.raises(ValidationError, match="exact configured plan"):
        _source(
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
                            "maxReceiveCount": "5",
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
