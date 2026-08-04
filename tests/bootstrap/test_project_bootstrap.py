"""Batteries-included state and runtime-image bootstrap tests."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import (
    ProjectBootstrapError,
    RuntimeImagePublisher,
    StateBucketBootstrap,
    active_admin_member,
    wait_for_service_account_impersonation,
)

if TYPE_CHECKING:
    from pathlib import Path


class _Runner:
    def __init__(self, *, bucket_exists: bool = False) -> None:
        self.bucket_exists = bucket_exists
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd.is_absolute()
        self.commands.append(args)
        if args[:4] == ("gcloud", "storage", "buckets", "describe"):
            return subprocess.CompletedProcess(args, 0 if self.bucket_exists else 1, stdout="")
        if args[:4] == ("gcloud", "artifacts", "docker", "images"):
            return subprocess.CompletedProcess(args, 0, stdout="sha256:" + "a" * 64 + "\n")
        if args[:3] == ("gcloud", "auth", "list"):
            return subprocess.CompletedProcess(args, 0, stdout="operator@example.invalid\n")
        return subprocess.CompletedProcess(args, 0, stdout="")


def test_state_bucket_bootstrap_creates_only_the_hardened_backend(tmp_path: Path) -> None:
    runner = _Runner()

    created = StateBucketBootstrap(cwd=tmp_path, runner=runner).ensure(
        project="unit-project",
        bucket="unit-project-dander-state",
        location="US",
        apply=True,
    )

    assert created
    create = next(command for command in runner.commands if "create" in command)
    assert "--uniform-bucket-level-access" in create
    assert "--public-access-prevention" in create
    update = next(command for command in runner.commands if "update" in command)
    assert "--versioning" in update
    assert "--update-labels=managed-by=dander,purpose=terraform-state" in update


def test_runtime_image_publisher_returns_an_immutable_digest(tmp_path: Path) -> None:
    for name in ("Dockerfile", "pyproject.toml", "uv.lock", "dander.yaml"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    for directory in ("src", "connectors", "models"):
        path = tmp_path / directory
        path.mkdir()
        (path / "content.txt").write_text(directory, encoding="utf-8")
    runner = _Runner()

    image = RuntimeImagePublisher(tmp_path, runner=runner).publish(
        project="unit-project",
        region="us-central1",
    )

    assert image == ("us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64)
    build = next(
        command for command in runner.commands if command[:3] == ("docker", "buildx", "build")
    )
    assert "--platform" in build and "linux/amd64" in build and "--push" in build


def test_runtime_image_publisher_accepts_installed_project_context(tmp_path: Path) -> None:
    for name in ("Dockerfile", "README.md", "dander.yaml"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    for directory in ("connectors", "models"):
        path = tmp_path / directory
        path.mkdir()
        (path / "content.txt").write_text(directory, encoding="utf-8")

    image = RuntimeImagePublisher(tmp_path, runner=_Runner()).publish(
        project="unit-project",
        region="us-central1",
    )

    assert image.endswith("@sha256:" + "a" * 64)


def test_runtime_image_tag_ignores_local_state_but_tracks_infrastructure(tmp_path: Path) -> None:
    for name in ("Dockerfile", "dander.yaml"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    for directory in ("connectors", "models", "infra"):
        path = tmp_path / directory
        path.mkdir()
        (path / "content.txt").write_text(directory, encoding="utf-8")
    runner = _Runner()
    publisher = RuntimeImagePublisher(tmp_path, runner=runner)

    publisher.publish(project="unit-project", region="us-central1")
    (tmp_path / "infra" / "local.tfstate").write_text("state", encoding="utf-8")
    publisher.publish(project="unit-project", region="us-central1")
    (tmp_path / "infra" / "content.txt").write_text("changed", encoding="utf-8")
    publisher.publish(project="unit-project", region="us-central1")

    tags = [
        command[command.index("-t") + 1]
        for command in runner.commands
        if command[:3] == ("docker", "buildx", "build")
    ]
    assert tags[0] == tags[1]
    assert tags[2] != tags[1]


def test_runtime_image_tag_tracks_graph_content(tmp_path: Path) -> None:
    for name in ("Dockerfile", "dander.yaml"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    for directory in ("connectors", "graphs", "models"):
        path = tmp_path / directory
        path.mkdir()
        (path / "content.txt").write_text(directory, encoding="utf-8")
    runner = _Runner()
    publisher = RuntimeImagePublisher(tmp_path, runner=runner)

    publisher.publish(project="unit-project", region="us-central1", tag_prefix="candidate")
    (tmp_path / "graphs" / "content.txt").write_text("changed", encoding="utf-8")
    publisher.publish(project="unit-project", region="us-central1", tag_prefix="candidate")

    tags = [
        command[command.index("-t") + 1]
        for command in runner.commands
        if command[:3] == ("docker", "buildx", "build")
    ]
    assert tags[0] != tags[1]
    assert all(":candidate-" in tag for tag in tags)


def test_active_admin_member_uses_the_authenticated_gcloud_user(tmp_path: Path) -> None:
    assert active_admin_member(cwd=tmp_path, runner=_Runner()) == ("user:operator@example.invalid")


def test_impersonation_readiness_retries_eventual_iam_propagation(tmp_path: Path) -> None:
    return_codes = iter((1, 1, 0))
    commands: list[tuple[str, ...]] = []
    delays: list[float] = []

    def runner(
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, next(return_codes), stdout="")

    wait_for_service_account_impersonation(
        service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
        project="unit-project",
        cwd=tmp_path,
        runner=runner,
        attempts=3,
        delay_seconds=0.25,
        sleep=delays.append,
    )

    assert len(commands) == 3
    assert delays == [0.25, 0.25]
    assert commands[0] == (
        "gcloud",
        "auth",
        "print-access-token",
        "--impersonate-service-account=dander-bootstrap@unit-project.iam.gserviceaccount.com",
        "--project=unit-project",
    )


def test_impersonation_readiness_fails_after_bounded_wait(tmp_path: Path) -> None:
    def runner(
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="")

    with pytest.raises(ProjectBootstrapError, match="did not become usable within 1 seconds"):
        wait_for_service_account_impersonation(
            service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
            project="unit-project",
            cwd=tmp_path,
            runner=runner,
            attempts=2,
            delay_seconds=0.5,
            sleep=lambda _seconds: None,
        )
