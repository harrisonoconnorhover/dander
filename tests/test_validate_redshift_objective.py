"""Focused checks for the canonical exact-RC32 Redshift launcher preflight."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import validate_redshift_objective as validator

REMAINING_MODULES = (
    "scripts.benchmarks.redshift_bulk_phase8",
    "scripts.benchmarks.redshift_incremental_phase8",
    "scripts.benchmarks.redshift_concurrency_phase8",
    "scripts.benchmarks.redshift_transform_phase8",
    "scripts.benchmarks.redshift_bounded_memory_phase8",
)


@pytest.mark.parametrize("module", REMAINING_MODULES)
def test_canonical_contract_accepts_each_remaining_rc32_cell(
    tmp_path: Path,
    module: str,
) -> None:
    objective, repository = _objective(tmp_path, module)

    payload = validator.validate_objective(objective, repository_root=repository)

    contract = payload["configuration"]["launcher_contract"]  # type: ignore[index]
    assert contract == validator.canonical_launcher_contract(module)


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    [
        (("configuration", "fargate_harness", "task_cpu_units"), 1024, "task_cpu_units"),
        (("configuration", "fargate_harness", "task_memory_mib"), 8192, "task_memory_mib"),
        (
            ("configuration", "fargate_harness", "runtime_cpu_architecture"),
            "X86_64",
            "architecture",
        ),
        (
            ("configuration", "fargate_harness", "candidate_python_executable"),
            "/app/.venv/bin/python",
            "executable",
        ),
        (("configuration", "fargate_harness", "task_user"), "0:0", "task_user"),
        (("configuration", "fargate_harness", "task_read_only_root"), False, "read_only_root"),
        (
            ("configuration", "fargate_harness", "task_writable_paths"),
            ["/tmp", "/app"],
            "writable_paths",
        ),
        (
            ("configuration", "fargate_harness", "harness_working_directory"),
            "/app",
            "working_directory",
        ),
        (("configuration", "fargate_harness", "harness_environment"), {}, "environment"),
        (
            ("configuration", "fargate_harness", "state_machine_retry_states"),
            1,
            "state_machine_retry_states",
        ),
        (
            ("configuration", "execution", "manual_candidate_executions"),
            2,
            "manual_candidate_executions",
        ),
        (
            ("configuration", "execution", "provider_operation_retries"),
            1,
            "provider_operation_retries",
        ),
        (
            ("configuration", "data_plane", "redshift_serverless_daily_usage_limit_rpu_hours"),
            8,
            "usage limit",
        ),
        (
            ("configuration", "task_role", "required_global_actions"),
            ["tag:GetResources"],
            "task role",
        ),
        (("configuration", "cleanup", "direct_owned_resource_inventories_empty"), False, "cleanup"),
        (
            ("configuration", "diagnostics", "provider_exception_messages"),
            True,
            "diagnostics",
        ),
        (
            (
                "budget_allocation",
                "remaining_aggregate_ceiling_after_full_objective_reservation_usd",
            ),
            "9.99",
            "budget",
        ),
    ],
)
def test_static_drift_fails_before_pr_creation(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: object,
    message: str,
) -> None:
    objective, repository = _objective(tmp_path, REMAINING_MODULES[0])
    payload: dict[str, Any] = json.loads(objective.read_text(encoding="utf-8"))
    target: dict[str, Any] = payload
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = replacement
    objective.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(validator.ObjectiveValidationError, match=message):
        validator.validate_objective(objective, repository_root=repository)


@pytest.mark.parametrize(
    ("contract_section", "field", "replacement"),
    [
        ("iam_readiness", "maximum_checks", 0),
        ("connection", "integrated_iam", True),
        ("connection", "sslmode", "prefer"),
        ("connection", "query", "SELECT current_user"),
    ],
)
def test_protected_launcher_contract_sections_are_not_overridable(
    tmp_path: Path,
    contract_section: str,
    field: str,
    replacement: object,
) -> None:
    objective, repository = _objective(tmp_path, REMAINING_MODULES[1])
    payload: dict[str, Any] = json.loads(objective.read_text(encoding="utf-8"))
    payload["configuration"]["launcher_contract"][contract_section][field] = replacement
    objective.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(validator.ObjectiveValidationError, match="not canonical"):
        validator.validate_objective(objective, repository_root=repository)


def test_bundle_must_be_exact_and_hashes_must_match_protected_code(tmp_path: Path) -> None:
    objective, repository = _objective(tmp_path, REMAINING_MODULES[2])
    payload: dict[str, Any] = json.loads(objective.read_text(encoding="utf-8"))
    payload["configuration"]["fargate_harness"]["harness_bundle_contains"].append("unrelated.txt")
    objective.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(validator.ObjectiveValidationError, match="bundle members"):
        validator.validate_objective(objective, repository_root=repository)

    objective, repository = _objective(tmp_path / "hash", REMAINING_MODULES[2])
    payload = json.loads(objective.read_text(encoding="utf-8"))
    payload["configuration"]["execution"]["harness_sha256"] = "0" * 64
    objective.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(validator.ObjectiveValidationError, match="protected code"):
        validator.validate_objective(objective, repository_root=repository)


def test_local_rc32_image_smoke_uses_exact_bundle_and_fail_closed_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objective, repository = _objective(tmp_path, REMAINING_MODULES[3])
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(f'["example.invalid/dander@{validator.RC32_DIGEST}"] arm64\n'),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.validate_redshift_objective.subprocess.run", run)

    validator.smoke_candidate_commands(
        [objective],
        repository_root=repository,
        image="local-rc32",
    )

    container = calls[1]
    assert "--read-only" in container
    assert "/tmp:rw,noexec,nosuid,size=128m" in container
    assert container[container.index("--user") + 1] == "65532:65532"
    shell = container[-1]
    assert "import scripts.benchmarks.redshift_transform_phase8" in shell
    assert (
        "dander qualification-run scripts/benchmarks/redshift_transform_phase8.py --help" in shell
    )
    assert "/app/.venv/bin" in shell


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["HANDOFF.md", "docs/evidence/phase8/2026-08-24/result.json"], "objective"),
        (
            ["docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-bulk-objectives.json"],
            "objective",
        ),
        (
            [
                "scripts/benchmarks/redshift_bulk_phase8.py",
                "tests/portability/test_redshift_bulk_phase8_benchmark.py",
            ],
            "benchmark",
        ),
        (["docs/evidence/phase8/2026-08-24/bigquery-objectives.json"], "full"),
        (["src/dander/providers/redshift/runtime.py"], "full"),
        (["Dockerfile"], "full"),
    ],
)
def test_ci_scope_is_proportional_to_changed_paths(
    paths: list[str],
    expected: str,
) -> None:
    assert validator.classify_ci_scope(paths) == expected


def test_ci_routes_light_changes_away_from_distribution_and_container_builds() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "--ci-scope" in workflow
    assert "Objective and evidence checks" in workflow
    assert "Benchmark and harness checks" in workflow
    assert "Validate AWS-native qualification Terraform" in workflow
    assert workflow.count("if: needs.scope.outputs.lane == 'full'") >= 20
    container = workflow.split("  container:", maxsplit=1)[1].split("  secrets:", maxsplit=1)[0]
    assert (
        "Build immutable-check image\n        if: needs.scope.outputs.lane == 'full'" in container
    )
    assert (
        "Scan OCI lifecycle controller\n        if: needs.scope.outputs.lane == 'full'" in container
    )


def _objective(tmp_path: Path, module: str) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    script = module.replace(".", "/") + ".py"
    dependencies = validator.BENCHMARK_DEPENDENCIES[module]
    files = {
        "scripts/__init__.py",
        "scripts/benchmarks/__init__.py",
        "scripts/benchmarks/redshift.py",
        script,
        *dependencies,
    }
    for relative in files:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {relative}\n", encoding="utf-8")

    cell = module.rsplit(".", maxsplit=1)[-1].removeprefix("redshift_").removesuffix("_phase8")
    relative_objective = Path(
        f"docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-{cell}-objectives.json"
    )
    objective = repository / relative_objective
    objective.parent.mkdir(parents=True, exist_ok=True)
    contract = validator.canonical_launcher_contract(module)
    bundle = sorted({relative_objective.as_posix(), *files})
    execution: dict[str, object] = {
        "harness_sha256": _sha256(repository / script),
        "shared_harness_sha256": _sha256(repository / "scripts/benchmarks/redshift.py"),
        "manual_candidate_executions": 1,
        "automatic_candidate_retry": False,
        "provider_operation_retries": 0,
        "aws_max_attempts": 1,
        "candidate_command": (
            f"cd /tmp/harness && PYTHONPATH=/tmp/harness dander qualification-run {script}"
        ),
    }
    if dependencies:
        execution["bulk_harness_sha256"] = _sha256(
            repository / "scripts/benchmarks/redshift_bulk_phase8.py"
        )
    fargate = {
        path[-1]: deepcopy(value)
        for path, value in validator.PROFILE_FIELDS
        if path[:2] == ("configuration", "fargate_harness")
    }
    fargate.update(
        {
            "cluster_executions": 1,
            "state_machine_executions": 1,
            "state_machine_retry_states": 0,
            "ecs_task_retries": 0,
            "container_restarts": 0,
            "automatic_retry": False,
            "task_entrypoint": ["/bin/sh", "-c"],
            "harness_bundle_contains": bundle,
            "selected_benchmark_module": module,
            "module_import_smoke": f"python -c 'import {module}'",
        }
    )
    payload = {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.50", "approval_reference": "approved"},
        "budget_allocation": {
            "aggregate_ceiling_usd": "20.00",
            "provider_measured_or_conservative_before_objective_usd": "13.00",
            "existing_reserved_before_objective_usd": "1.00",
            "objective_reservation_usd": "0.50",
            "remaining_aggregate_ceiling_after_full_objective_reservation_usd": "5.50",
        },
        "configuration": {
            "launcher_contract": contract,
            "candidate": {
                "release_version": validator.RC32_VERSION,
                "git_commit": validator.RC32_GIT_COMMIT,
                "target_image": validator.RC32_IMAGE,
                "mutable_tag_selection": False,
            },
            "data_plane": {
                "terraform_root": "infra/qualification/aws-native",
                "redshift_serverless_base_capacity_rpu": 8,
                "redshift_serverless_daily_usage_limit_rpu_hours": 5,
                "terraform_provider_operation_retries": 0,
            },
            "task_role": deepcopy(validator.TASK_ROLE),
            "fargate_harness": fargate,
            "execution": execution,
            "diagnostics": deepcopy(validator.DIAGNOSTICS),
            "cleanup": deepcopy(validator.CLEANUP),
        },
    }
    objective.write_text(json.dumps(payload), encoding="utf-8")
    return objective, repository


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
