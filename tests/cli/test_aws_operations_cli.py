"""Thin CLI coverage for manifest-bound Fargate operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import dander.cli.aws_command as aws_command
from dander.cli.main import app
from dander.providers.fargate import (
    FargateDeploymentVerification,
    FargateExecution,
    FargateLogEvent,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch

EXECUTION = (
    "arn:aws:states:us-east-1:123456789012:execution:dander-salesforce-crm-deadbeef:manual-unit"
)
IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64


class _Operations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.execution = FargateExecution(
            execution_arn=EXECUTION,
            name="manual-unit",
            state="succeeded",
            run_id="salesforce_crm:unit",
        )

    def start(self, *, execution_name: str | None = None) -> FargateExecution:
        self.calls.append(("start", execution_name))
        return self.execution

    def latest(self) -> FargateExecution:
        self.calls.append(("latest", None))
        return self.execution

    def describe(self, execution_arn: str) -> FargateExecution:
        self.calls.append(("describe", execution_arn))
        return self.execution

    def logs(self, execution_arn: str, *, limit: int) -> tuple[FargateLogEvent, ...]:
        self.calls.append(("logs", (execution_arn, limit)))
        return (FargateLogEvent(timestamp=1, message="runtime complete"),)

    def cancel(self, execution_arn: str) -> FargateExecution:
        self.calls.append(("cancel", execution_arn))
        return self.execution

    def replay(
        self,
        execution_arn: str,
        *,
        execution_name: str | None = None,
    ) -> FargateExecution:
        self.calls.append(("replay", (execution_arn, execution_name)))
        return self.execution

    def verify(self, *, expected_image: str) -> FargateDeploymentVerification:
        self.calls.append(("verify", expected_image))
        return FargateDeploymentVerification(
            state_machine="machine",
            cluster="cluster",
            schedule="schedule",
            schedule_state="DISABLED",
            task_definition="task",
            image=expected_image,
            log_group="logs",
            repository="repository",
        )


def _install_fake(monkeypatch: MonkeyPatch) -> tuple[_Operations, dict[str, object]]:
    operations = _Operations()
    binding: dict[str, object] = {}

    def factory(**kwargs: object) -> _Operations:
        binding.update(kwargs)
        return operations

    monkeypatch.setattr(aws_command, "_fargate_operations", factory)
    return operations, binding


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--deployment",
        "aws_fargate",
        "--pipeline",
        "salesforce_crm",
        "--config",
        str(tmp_path / "dander.yaml"),
        "--aws-profile",
        "dander-deploy",
    ]


def test_status_and_logs_are_bound_to_the_selected_manifest_pipeline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations, binding = _install_fake(monkeypatch)

    status = CliRunner().invoke(
        app,
        ["aws", "status", *_base_args(tmp_path), "--execution-arn", EXECUTION],
    )
    logs = CliRunner().invoke(
        app,
        ["aws", "logs", *_base_args(tmp_path), "--execution-arn", EXECUTION, "--limit", "7"],
    )

    assert status.exit_code == 0, status.output
    assert logs.exit_code == 0, logs.output
    assert '"state": "succeeded"' in status.output
    assert '"message": "runtime complete"' in logs.output
    assert operations.calls == [("describe", EXECUTION), ("logs", (EXECUTION, 7))]
    assert binding == {
        "config": tmp_path / "dander.yaml",
        "deployment": "aws_fargate",
        "pipeline": "salesforce_crm",
        "name": "dander",
        "aws_profile": "dander-deploy",
    }


@pytest.mark.parametrize(
    ("command", "extra", "expected"),
    [
        ("run", ["--execution-name", "manual-unit"], ("start", "manual-unit")),
        ("cancel", ["--execution-arn", EXECUTION], ("cancel", EXECUTION)),
        (
            "replay",
            ["--execution-arn", EXECUTION, "--execution-name", "replay-unit"],
            ("replay", (EXECUTION, "replay-unit")),
        ),
    ],
)
def test_mutating_operations_require_confirmation_and_invoke_one_action(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    command: str,
    extra: list[str],
    expected: tuple[str, object],
) -> None:
    operations, _binding = _install_fake(monkeypatch)

    refused = CliRunner().invoke(
        app,
        ["aws", command, *_base_args(tmp_path), *extra],
        input="n\n",
    )
    accepted = CliRunner().invoke(
        app,
        ["aws", command, *_base_args(tmp_path), *extra],
        input="y\n",
    )

    assert refused.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert operations.calls == [expected]


def test_verify_reports_the_exact_expected_image(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations, _binding = _install_fake(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["aws", "verify", *_base_args(tmp_path), "--expected-image", IMAGE],
    )

    assert result.exit_code == 0, result.output
    assert IMAGE in result.output
    assert operations.calls == [("verify", IMAGE)]
