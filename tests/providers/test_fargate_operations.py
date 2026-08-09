"""Provider-native Fargate execution, logs, replay, and verification tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml

from dander.project import prepare_version_one_migration
from dander.providers.fargate import (
    FargateBinding,
    FargateOperationError,
    FargateOperations,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

ACCOUNT = "123456789012"
REGION = "us-east-1"
PIPELINE = "salesforce_crm"
RESOURCE = (
    "dander-salesforce-crm-"
    + hashlib.sha1(PIPELINE.encode(), usedforsecurity=False).hexdigest()[:8]
)
MACHINE = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{RESOURCE}"
EXECUTION = MACHINE.replace(":stateMachine:", ":execution:") + ":manual-unit"
TASK_ID = "a" * 32
TASK_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/dander/{TASK_ID}"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/dander@sha256:" + "b" * 64


def _binding(tmp_path: Path, *, paused: bool = True) -> FargateBinding:
    return FargateBinding(
        account_id=ACCOUNT,
        region=REGION,
        deployment_name="aws_fargate",
        pipeline_id=PIPELINE,
        resource_name=RESOURCE,
        state_machine_arn=MACHINE,
        cluster_name="dander",
        log_group_name=f"/dander/dander/{PIPELINE}",
        schedule_paused=paused,
        project_dir=tmp_path,
    )


class _Runner:
    def __init__(self, responses: Mapping[tuple[str, str], list[dict[str, object]]]) -> None:
        self.responses: defaultdict[tuple[str, str], deque[dict[str, object]]] = defaultdict(deque)
        for key, values in responses.items():
            self.responses[key].extend(values)
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd.is_absolute() and check and capture_output and text
        self.commands.append(args)
        service_index = 3 if args[1:2] == ("--profile",) else 1
        key = (args[service_index], args[service_index + 1])
        if not self.responses[key]:
            raise AssertionError(f"No fake AWS response for {key}")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(self.responses[key].popleft()),
        )


def _operations(
    tmp_path: Path,
    responses: Mapping[tuple[str, str], list[dict[str, object]]],
    *,
    paused: bool = True,
) -> tuple[FargateOperations, _Runner]:
    runner = _Runner(responses)
    operations = FargateOperations(
        _binding(tmp_path, paused=paused),
        aws_profile="dander-deploy",
        runner=runner,
        clock=lambda: datetime(2026, 8, 8, 21, 0, tzinfo=UTC),
        nonce=lambda: "deadbeef",
    )
    return operations, runner


def _describe(
    *,
    status: str = "SUCCEEDED",
    execution_arn: str = EXECUTION,
    output: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "executionArn": execution_arn,
        "status": status,
        "startDate": "2026-08-08T21:00:00Z",
    }
    if status != "RUNNING":
        payload["stopDate"] = "2026-08-08T21:01:00Z"
    if output is not None:
        payload["output"] = json.dumps(output)
    return payload


def test_binding_resolves_the_exact_manifest_pipeline_and_terraform_name(tmp_path: Path) -> None:
    project = tmp_path / "dander.yaml"
    project.write_text(
        """
version: 1
platform:
  region: us-central1
  safety:
    require_guarded_free_tier: false
pipelines:
  salesforce_crm:
    source: salesforce
    models: []
    build_models: false
    paused: true
    resources:
      job: dander-salesforce-crm
      runtime_service_account: dander-salesforce-run
      scheduler_service_account: dander-salesforce-sched
""".strip(),
        encoding="utf-8",
    )
    connectors = tmp_path / "connectors"
    connectors.mkdir()
    (connectors / "salesforce.yaml").write_text(
        """
name: salesforce
base_url: https://example.com
auth_strategy: none
endpoints:
  - name: records
    path: /records
    primary_key: [id]
    raw_schema:
      - {name: id, type: STRING, mode: REQUIRED}
""".strip(),
        encoding="utf-8",
    )
    migration = prepare_version_one_migration(project)
    project.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    deployment = platforms["deployments"].pop("gcp_cloud_run")
    deployment["launcher"] = {
        "provider": "fargate",
        "region": REGION,
        "aws_account_id": ACCOUNT,
        "google_workload_identity_audience": (
            "//iam.googleapis.com/projects/123456789012/locations/global/"
            "workloadIdentityPools/dander-aws/providers/dander-aws"
        ),
        "subnet_ids": ["subnet-0123456789abcdef0"],
        "security_group_ids": ["sg-0123456789abcdef0"],
    }
    deployment["runtime"]["memory"] = "2Gi"
    deployment["safety"]["require_guarded_free_tier"] = False
    platforms["deployments"]["aws_fargate"] = deployment
    (tmp_path / "dander.platforms.yaml").write_text(yaml.safe_dump(platforms), encoding="utf-8")

    binding = FargateBinding.from_project(
        config=project,
        deployment="aws_fargate",
        pipeline_id=PIPELINE,
    )

    assert binding.resource_name == RESOURCE
    assert binding.state_machine_arn == MACHINE
    assert binding.log_group_name == f"/dander/dander/{PIPELINE}"
    assert binding.schedule_paused is True


def test_start_uses_exact_state_machine_and_operator_correlation(tmp_path: Path) -> None:
    started_arn = MACHINE.replace(":stateMachine:", ":execution:") + ":manual-unit"
    operations, runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "start-execution"): [
                {"executionArn": started_arn, "startDate": "2026-08-08T21:00:00Z"}
            ]
        },
    )

    execution = operations.start(execution_name="manual-unit")

    assert execution.execution_arn == started_arn
    assert execution.state == "running"
    command = runner.commands[0]
    assert command[:5] == (
        "aws",
        "--profile",
        "dander-deploy",
        "stepfunctions",
        "start-execution",
    )
    assert command[command.index("--state-machine-arn") + 1] == MACHINE
    request = json.loads(command[command.index("--input") + 1])
    assert request == {
        "deployment_revision": "manual",
        "scheduled_time": "2026-08-08T21:00:00Z",
        "scheduler_attempt": 1,
        "scheduler_execution_id": "manual:manual-unit",
    }


def test_latest_status_normalizes_runtime_output_without_provider_noise(tmp_path: Path) -> None:
    operations, _runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "list-executions"): [{"executions": [{"executionArn": EXECUTION}]}],
            ("stepfunctions", "describe-execution"): [
                _describe(
                    output={
                        "status": "succeeded",
                        "run_id": f"{PIPELINE}:2026-08-08T21:00:00Z",
                        "task_arn": TASK_ARN,
                        "container_exit_code": 0,
                    }
                )
            ],
        },
    )

    execution = operations.latest()

    assert execution is not None
    assert execution.state == "succeeded"
    assert execution.task_arn == TASK_ARN
    assert execution.container_exit_code == 0
    assert execution.run_id == f"{PIPELINE}:2026-08-08T21:00:00Z"


def test_failed_status_recovers_only_normalized_fields_from_execution_history(
    tmp_path: Path,
) -> None:
    failure = {
        "failure_code": "runtime_permanent_failure",
        "run_id": f"{PIPELINE}:2026-08-08T21:00:00Z",
        "task_arn": TASK_ARN,
        "container_exit_code": 1,
        "provider_error": "must not be returned",
    }
    operations, _runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-execution"): [_describe(status="FAILED")],
            ("stepfunctions", "get-execution-history"): [
                {"events": [{"stateExitedEventDetails": {"output": json.dumps(failure)}}]}
            ],
        },
    )

    execution = operations.describe(EXECUTION)

    assert execution.state == "failed"
    assert execution.failure_code == "runtime_permanent_failure"
    assert execution.run_id == f"{PIPELINE}:2026-08-08T21:00:00Z"
    assert execution.task_arn == TASK_ARN
    assert execution.container_exit_code == 1
    assert "provider_error" not in execution.as_dict()


def test_logs_find_the_task_in_execution_history_and_use_its_exact_stream(tmp_path: Path) -> None:
    operations, runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-execution"): [_describe(status="RUNNING")],
            ("stepfunctions", "get-execution-history"): [
                {
                    "events": [
                        {
                            "taskSubmittedEventDetails": {
                                "output": json.dumps({"Tasks": [{"TaskArn": TASK_ARN}]})
                            }
                        }
                    ]
                }
            ],
            ("logs", "filter-log-events"): [
                {"events": [{"timestamp": 1234, "message": "runtime started"}]}
            ],
        },
    )

    events = operations.logs(EXECUTION, limit=25)

    assert events[0].message == "runtime started"
    logs = runner.commands[-1]
    assert logs[logs.index("--log-group-name") + 1] == f"/dander/dander/{PIPELINE}"
    assert logs[logs.index("--log-stream-name-prefix") + 1] == f"runtime/dander/{TASK_ID}"
    assert logs[logs.index("--limit") + 1] == "25"


def test_cancel_only_stops_an_owned_running_execution(tmp_path: Path) -> None:
    operations, runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-execution"): [_describe(status="RUNNING")],
            ("stepfunctions", "stop-execution"): [{"stopDate": "2026-08-08T21:00:30Z"}],
        },
    )

    cancelled = operations.cancel(EXECUTION)

    assert cancelled.state == "cancelled"
    assert cancelled.failure_code == "operator_cancelled"
    assert (runner.commands[-1][3], runner.commands[-1][4]) == (
        "stepfunctions",
        "stop-execution",
    )


def test_replay_requires_terminal_execution_and_starts_a_fresh_name(tmp_path: Path) -> None:
    replay_arn = MACHINE.replace(":stateMachine:", ":execution:") + ":replay-unit"
    operations, runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-execution"): [_describe()],
            ("stepfunctions", "start-execution"): [
                {"executionArn": replay_arn, "startDate": "2026-08-08T21:02:00Z"}
            ],
        },
    )

    replay = operations.replay(EXECUTION, execution_name="replay-unit")

    assert replay.name == "replay-unit"
    request = json.loads(runner.commands[-1][runner.commands[-1].index("--input") + 1])
    assert request["scheduler_execution_id"] == "replay:manual-unit"


def test_replay_rejects_a_running_execution_without_starting_another(tmp_path: Path) -> None:
    operations, runner = _operations(
        tmp_path,
        {("stepfunctions", "describe-execution"): [_describe(status="RUNNING")]},
    )

    with pytest.raises(FargateOperationError, match="terminal"):
        operations.replay(EXECUTION)

    assert len(runner.commands) == 1


def test_execution_arn_must_belong_to_the_bound_pipeline(tmp_path: Path) -> None:
    operations, runner = _operations(tmp_path, {})

    with pytest.raises(FargateOperationError, match="selected pipeline"):
        operations.describe(
            f"arn:aws:states:{REGION}:{ACCOUNT}:execution:other-pipeline:manual-unit"
        )

    assert runner.commands == []


def test_verify_checks_controller_schedule_image_logs_cluster_and_registry(tmp_path: Path) -> None:
    operations, _runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-state-machine"): [{"status": "ACTIVE"}],
            ("ecs", "describe-clusters"): [
                {
                    "clusters": [
                        {
                            "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/dander",
                            "status": "ACTIVE",
                        }
                    ],
                    "failures": [],
                }
            ],
            ("scheduler", "get-schedule"): [
                {
                    "Arn": (f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/default/{RESOURCE}"),
                    "State": "DISABLED",
                }
            ],
            ("logs", "describe-log-groups"): [
                {"logGroups": [{"logGroupName": f"/dander/dander/{PIPELINE}"}]}
            ],
            ("ecs", "describe-task-definition"): [
                {
                    "taskDefinition": {
                        "taskDefinitionArn": (
                            f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{RESOURCE}:1"
                        ),
                        "status": "ACTIVE",
                        "containerDefinitions": [
                            {
                                "image": IMAGE,
                                "readonlyRootFilesystem": True,
                                "user": "65532:65532",
                            }
                        ],
                    }
                }
            ],
            ("ecr", "describe-repositories"): [
                {
                    "repositories": [
                        {
                            "repositoryArn": (f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/dander"),
                            "imageTagMutability": "IMMUTABLE",
                            "encryptionConfiguration": {"encryptionType": "KMS"},
                            "imageScanningConfiguration": {"scanOnPush": True},
                        }
                    ]
                }
            ],
        },
    )

    result = operations.verify(expected_image=IMAGE)

    assert result.image == IMAGE
    assert result.schedule_state == "DISABLED"
    assert result.state_machine == MACHINE


def test_verify_fails_when_schedule_state_differs_from_manifest(tmp_path: Path) -> None:
    operations, _runner = _operations(
        tmp_path,
        {
            ("stepfunctions", "describe-state-machine"): [{"status": "ACTIVE"}],
            ("ecs", "describe-clusters"): [
                {"clusters": [{"clusterArn": "cluster", "status": "ACTIVE"}], "failures": []}
            ],
            ("scheduler", "get-schedule"): [{"Arn": "schedule", "State": "ENABLED"}],
        },
    )

    with pytest.raises(FargateOperationError, match="schedule"):
        operations.verify(expected_image=IMAGE)
