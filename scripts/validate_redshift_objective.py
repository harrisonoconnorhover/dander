#!/usr/bin/env python3
"""Validate new exact-RC32 AWS-native Redshift objectives before PR creation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

RC32_VERSION = "0.9.0rc32"
RC32_GIT_COMMIT = "0d648a622fa2b0240a3b7b5fb8b7151445591bca"
RC32_DIGEST = "sha256:0c2717701a80003ca4e898485569c1f3728464845e735455bea68016b5975d63"
RC32_ARM64_MANIFEST_DIGEST = (
    "sha256:93d359a6454ba57d41a31a618edb889c459cc5838184f6ea110aa89c63d35e53"
)
RC32_IMAGE = "184463061564.dkr.ecr.us-east-1.amazonaws.com/dander:0.9.0rc32"
LAUNCHER_PREFLIGHT = "scripts/benchmarks/redshift_launcher_preflight.py"
QUERY_BOUNDARY_SCHEMA = "io.dander.phase8.redshift-query-boundary-diagnostic-approval/v1"
QUERY_BOUNDARY_SCRIPT = "scripts/benchmarks/redshift_query_boundary_diagnostic_phase8.py"
QUERY_BOUNDARY_C13_OBJECTIVE = (
    "docs/evidence/phase8/2026-08-24/"
    "aws-native-rc32-redshift-query-boundary-diagnostic-objective.json"
)
QUERY_BOUNDARY_C13_HARNESS_SHA256 = (
    "88b93a7345d195510f659bb3649ac7414649ae7dbb17ee005541c6d40900d8f1"
)
QUERY_BOUNDARY_STAGES = [
    "get_credentials",
    "verified_postgres_tls_handshake",
    "psycopg_connector",
    "psycopg_validation_query",
    "redshift_connector",
    "redshift_validation_query",
]

BENCHMARK_DEPENDENCIES = {
    "scripts.benchmarks.redshift_bulk_phase8": (),
    "scripts.benchmarks.redshift_incremental_phase8": (
        "scripts/benchmarks/redshift_bulk_phase8.py",
    ),
    "scripts.benchmarks.redshift_concurrency_phase8": (
        "scripts/benchmarks/redshift_bulk_phase8.py",
    ),
    "scripts.benchmarks.redshift_transform_phase8": ("scripts/benchmarks/redshift_bulk_phase8.py",),
    "scripts.benchmarks.redshift_bounded_memory_phase8": (
        "scripts/benchmarks/redshift_bulk_phase8.py",
    ),
    "scripts.benchmarks.redshift_failure_phase8": ("scripts/benchmarks/redshift_bulk_phase8.py",),
}

PROFILE_FIELDS: tuple[tuple[tuple[str, ...], object], ...] = (
    (("configuration", "candidate", "release_version"), RC32_VERSION),
    (("configuration", "candidate", "git_commit"), RC32_GIT_COMMIT),
    (("configuration", "candidate", "image_index_digest"), RC32_DIGEST),
    (("configuration", "candidate", "target_image"), RC32_IMAGE),
    (("configuration", "candidate", "immutable_tag_selection"), True),
    (("configuration", "candidate", "mutable_tag_selection"), False),
    (("configuration", "data_plane", "terraform_root"), "infra/qualification/aws-native"),
    (("configuration", "data_plane", "redshift_serverless_base_capacity_rpu"), 8),
    (("configuration", "data_plane", "terraform_provider_operation_retries"), 0),
    (("configuration", "fargate_harness", "task_cpu_units"), 2048),
    (("configuration", "fargate_harness", "task_memory_mib"), 4096),
    (("configuration", "fargate_harness", "task_timeout_seconds"), 900),
    (("configuration", "fargate_harness", "runtime_cpu_architecture"), "ARM64"),
    (("configuration", "fargate_harness", "candidate_image_architecture"), "arm64"),
    (("configuration", "fargate_harness", "container_image_version_consistency"), "enabled"),
    (("configuration", "fargate_harness", "candidate_python_executable"), "python"),
    (("configuration", "fargate_harness", "candidate_cli_executable"), "dander"),
    (("configuration", "fargate_harness", "forbidden_executable_prefix"), "/app/.venv/bin/"),
    (("configuration", "fargate_harness", "task_entrypoint"), ["/bin/sh", "-c"]),
    (("configuration", "fargate_harness", "task_user"), "65532:65532"),
    (("configuration", "fargate_harness", "task_read_only_root"), True),
    (("configuration", "fargate_harness", "task_writable_paths"), ["/tmp"]),
    (("configuration", "fargate_harness", "task_tmpfs_path"), "/tmp"),
    (("configuration", "fargate_harness", "harness_working_directory"), "/tmp/harness"),
    (("configuration", "fargate_harness", "harness_import_root"), "/tmp/harness"),
    (("configuration", "fargate_harness", "harness_environment"), {"PYTHONPATH": "/tmp/harness"}),
    (("configuration", "fargate_harness", "harness_bundle_extract_to"), "/tmp/harness"),
    (("configuration", "fargate_harness", "harness_download_attempts"), 1),
    (("configuration", "fargate_harness", "cluster_executions"), 1),
    (("configuration", "fargate_harness", "state_machine_executions"), 1),
    (("configuration", "fargate_harness", "state_machine_retry_states"), 0),
    (("configuration", "fargate_harness", "ecs_task_retries"), 0),
    (("configuration", "fargate_harness", "container_restarts"), 0),
    (("configuration", "fargate_harness", "automatic_retry"), False),
    (("configuration", "execution", "manual_candidate_executions"), 1),
    (("configuration", "execution", "automatic_candidate_retry"), False),
    (("configuration", "execution", "provider_operation_retries"), 0),
    (("configuration", "execution", "aws_max_attempts"), 1),
)

TASK_ROLE = {
    "redshift_db_roles_tag": {"key": "RedshiftDbRoles", "value": "dander_runtime"},
    "required_global_actions": ["tag:GetResources", "tag:GetTagKeys"],
    "required_global_resource": "*",
    "required_scoped_actions": [
        "redshift-serverless:GetCredentials",
        "s3:DeleteObject",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
    ],
    "required_scoped_resource_binding": (
        "exact_owned_workgroup_and_staging_bucket_arns_after_apply"
    ),
}

DIAGNOSTICS = {
    "sanitized_fields": ["stage", "elapsed_ms", "exception_class"],
    "provider_exception_messages": False,
    "exact_owned_stdout_and_stderr_keys": True,
    "launcher_preflight_artifact": {
        "schema": "io.dander.phase8.aws-native-redshift-launcher-preflight/v1",
        "write_on_success": True,
        "write_on_failure": True,
        "server_side_encryption": "AES256",
    },
}

CLEANUP = {
    "delete_exact_harness_and_diagnostics": True,
    "deregister_exact_task_definition": True,
    "remove_exact_log_group_state_machine_roles_and_cluster": True,
    "destroy_launcher_before_data_plane": True,
    "terraform_state_entries_after_destroy": 0,
    "direct_owned_resource_inventories_empty": True,
}


class ObjectiveValidationError(ValueError):
    """An RC32 Redshift objective diverged from the protected launcher contract."""


def canonical_launcher_contract(benchmark_module: str) -> dict[str, object]:
    """Generate the non-overridable preflight for one supported benchmark module."""
    _benchmark_script(benchmark_module)
    return {
        "schema": "io.dander.phase8.aws-native-redshift-launcher/v1",
        "benchmark_module": benchmark_module,
        "image_manifest": {
            "digest": RC32_ARM64_MANIFEST_DIGEST,
            "architecture": "arm64",
            "parent_index_digest": RC32_DIGEST,
            "runtime_reference": RC32_IMAGE,
            "repository_tag_mutability": "IMMUTABLE",
            "verify_resolution_before_task_registration": True,
            "verify_resolution_after_task_stop": True,
        },
        "iam_readiness": {
            "required_before_candidate": True,
            "maximum_checks": 6,
            "interval_seconds": 5,
            "automatic_retry": False,
            "provider_operation_retries": 0,
            "checks": [
                "sts:GetCallerIdentity",
                "tag:GetResources",
                "tag:GetTagKeys",
                "redshift-serverless:GetCredentials",
                "s3:GetBucketLocation",
                "s3:GetObject",
            ],
        },
        "connection": {
            "credential_api": "redshift-serverless:GetCredentials",
            "integrated_iam": False,
            "ssl": True,
            "sslmode": "verify-full",
            "readiness_socket_timeout_seconds": 12,
            "readiness_maximum_probes": 4,
            "readiness_probe_interval_seconds": 5,
            "readiness_window_seconds": 115,
            "query": "SELECT 1",
            "must_succeed_before_candidate_command": True,
            "schema_or_workload_mutation_allowed": False,
        },
        "preflight": {
            "script": LAUNCHER_PREFLIGHT,
            "read_only": True,
            "artifact_on_success": True,
            "artifact_on_failure": True,
            "artifact_fields": ["stage", "elapsed_ms", "exception_class"],
        },
    }


def validate_objective(path: Path, *, repository_root: Path) -> dict[str, object]:
    """Validate one new objective and return its parsed payload."""
    payload = _load_object(path)
    if payload.get("schema") == QUERY_BOUNDARY_SCHEMA:
        _validate_query_boundary_diagnostic(path, payload, repository_root)
        return payload
    for field_path, expected in PROFILE_FIELDS:
        _require(_at(payload, field_path) == expected, f"{field_path[-1]} is not canonical")
    configuration = _mapping(payload.get("configuration"), "configuration")
    approved = _mapping(payload.get("approved_objectives"), "approved_objectives")
    _require(approved.get("release_version") == RC32_VERSION, "approved version is not RC32")
    _require(approved.get("git_commit") == RC32_GIT_COMMIT, "approved commit is not RC32")
    _require(approved.get("image_digest") == RC32_DIGEST, "approved digest is not RC32")
    contract = _mapping(configuration.get("launcher_contract"), "launcher_contract")
    module = str(contract.get("benchmark_module", ""))
    _require(contract == canonical_launcher_contract(module), "launcher contract is not canonical")
    _require(configuration.get("task_role") == TASK_ROLE, "task role is not canonical")
    _require(configuration.get("diagnostics") == DIAGNOSTICS, "diagnostics are not exact")
    _require(configuration.get("cleanup") == CLEANUP, "cleanup is not exact")
    _validate_usage_limit(configuration, module)
    _validate_runtime_bindings(configuration)
    _validate_bundle_and_hashes(path, configuration, module, repository_root)
    _validate_execution_authority(configuration, module)
    _validate_budget(payload)
    return payload


def validate_objectives(paths: list[Path], *, repository_root: Path) -> None:
    """Validate every supplied objective together."""
    _require(bool(paths), "at least one RC32 Redshift objective is required")
    for path in paths:
        validate_objective(path, repository_root=repository_root)


def _validate_usage_limit(configuration: dict[str, object], module: str) -> None:
    data_plane = _mapping(configuration.get("data_plane"), "data_plane")
    limit = data_plane.get("redshift_serverless_daily_usage_limit_rpu_hours")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ObjectiveValidationError("daily usage limit must be an integer from 1 through 5")
    _require(1 <= limit <= 5, "daily usage limit must be an integer from 1 through 5")
    if module == "scripts.benchmarks.redshift_failure_phase8":
        _require(limit >= 2, "failure-cell daily usage limit must be at least 2 RPU-hours")


def _validate_runtime_bindings(configuration: dict[str, object]) -> None:
    redshift = _mapping(configuration.get("redshift"), "redshift")
    data_plane = _mapping(configuration.get("data_plane"), "data_plane")
    fargate = _mapping(configuration.get("fargate_harness"), "fargate_harness")
    account = str(redshift.get("account_id", ""))
    region = str(redshift.get("region", ""))
    workgroup = str(redshift.get("workgroup_name", ""))
    expected_host = f"{workgroup}.{account}.{region}.redshift-serverless.amazonaws.com"
    expected_bucket = f"{workgroup}-{account}-staging"
    expected_role = f"arn:aws:iam::{account}:role/{workgroup}-redshift-copy"
    expected_cluster = f"arn:aws:ecs:{region}:{account}:cluster/{workgroup}"
    expected_task_definition = f"arn:aws:ecs:{region}:{account}:task-definition/{workgroup}:1"
    _require(len(account) == 12 and account.isdigit(), "Redshift account is not exact")
    _require(region == "us-east-1", "Redshift region is not exact")
    _require(redshift.get("host") == expected_host, "Redshift host is not exact")
    _require(redshift.get("database") == "analytics", "Redshift database is not exact")
    _require(redshift.get("staging_bucket") == expected_bucket, "staging bucket is not exact")
    _require(redshift.get("copy_role_arn") == expected_role, "copy role is not exact")
    _require(
        redshift.get("staging_prefix") == f"phase8/{RC32_VERSION}/staging",
        "staging prefix is not exact",
    )
    _require(data_plane.get("resource_name") == workgroup, "data-plane resource name drifted")
    _require(fargate.get("resource_prefix") == workgroup, "Fargate resource prefix drifted")
    _require(
        fargate.get("state_machine_cluster_arn") == expected_cluster,
        "state-machine cluster ARN is not exact",
    )
    _require(
        fargate.get("state_machine_task_definition_arn") == expected_task_definition,
        "state-machine task-definition ARN is not exact",
    )


def _validate_bundle_and_hashes(
    objective_path: Path,
    configuration: dict[str, object],
    module: str,
    repository_root: Path,
) -> None:
    fargate = _mapping(configuration.get("fargate_harness"), "fargate_harness")
    script = _benchmark_script(module)
    relative_objective = objective_path.resolve().relative_to(repository_root.resolve()).as_posix()
    expected_members = {
        relative_objective,
        "scripts/__init__.py",
        "scripts/benchmarks/__init__.py",
        "scripts/benchmarks/redshift.py",
        LAUNCHER_PREFLIGHT,
        script,
        *BENCHMARK_DEPENDENCIES[module],
    }
    members = {str(item) for item in _list(fargate.get("harness_bundle_contains"), "bundle")}
    _require(members == expected_members, "harness bundle members are not exact")
    for member in members:
        _require((repository_root / member).is_file(), f"bundle member is missing: {member}")
    _require(fargate.get("selected_benchmark_module") == module, "benchmark module drifted")
    _require(
        fargate.get("module_import_smoke") == f"python -c 'import {module}'",
        "benchmark import smoke is not exact",
    )
    expected_preflight = f"python {LAUNCHER_PREFLIGHT} --objective {relative_objective}"
    _require(
        fargate.get("launcher_preflight_command") == expected_preflight,
        "launcher preflight command is not exact",
    )
    staging_prefix = str(_mapping(configuration.get("redshift"), "redshift").get("staging_prefix"))
    _require(
        fargate.get("transient_launcher_preflight_key")
        == f"{staging_prefix}/diagnostics/launcher-preflight.json",
        "launcher preflight artifact key is not exact",
    )
    execution = _mapping(configuration.get("execution"), "execution")
    hashes = {
        "harness_sha256": repository_root / script,
        "shared_harness_sha256": repository_root / "scripts/benchmarks/redshift.py",
        "launcher_preflight_sha256": repository_root / LAUNCHER_PREFLIGHT,
    }
    if BENCHMARK_DEPENDENCIES[module]:
        hashes["bulk_harness_sha256"] = (
            repository_root / "scripts/benchmarks/redshift_bulk_phase8.py"
        )
    for field, source in hashes.items():
        _require(execution.get(field) == _sha256(source), f"{field} does not match protected code")


def _validate_execution_authority(
    configuration: dict[str, object],
    module: str,
) -> None:
    execution = _mapping(configuration.get("execution"), "execution")
    expected = _candidate_command(module)
    _require(execution.get("candidate_command") == expected, "candidate command is not exact")
    _require("/app/.venv/bin" not in expected, "candidate command uses a forbidden executable")
    if module == "scripts.benchmarks.redshift_failure_phase8":
        _require(
            execution.get("cost_observation_timeout_seconds") == 600,
            "failure-cell cost observation timeout must be 600 seconds",
        )
        cost_attribution = _mapping(configuration.get("cost_attribution"), "cost_attribution")
        _require(
            cost_attribution.get("metadata_observation_timeout_seconds") == 600,
            "failure-cell metadata observation timeout must be 600 seconds",
        )


def _validate_budget(payload: dict[str, object]) -> None:
    ceiling = _decimal(_at(payload, ("cost_ceiling", "amount_usd")), "cost ceiling")
    budget = _mapping(payload.get("budget_allocation"), "budget_allocation")
    aggregate = _decimal(budget.get("aggregate_ceiling_usd"), "aggregate ceiling")
    before = _decimal(
        budget.get("provider_measured_or_conservative_before_objective_usd"),
        "spend before objective",
    )
    reserved = _decimal(
        budget.get("existing_reserved_before_objective_usd"), "existing reservation"
    )
    objective = _decimal(budget.get("objective_reservation_usd"), "objective reservation")
    remaining = _decimal(
        budget.get("remaining_aggregate_ceiling_after_full_objective_reservation_usd"),
        "remaining aggregate ceiling",
    )
    _require(0 < ceiling <= objective, "per-cell cost ceiling exceeds its reservation")
    _require(
        aggregate - before - reserved - objective == remaining,
        "aggregate budget arithmetic does not reconcile",
    )
    _require(remaining >= 0, "objective exceeds aggregate spend authority")


def _validate_query_boundary_diagnostic(
    objective_path: Path,
    payload: dict[str, object],
    repository_root: Path,
) -> None:
    candidate = _mapping(payload.get("candidate"), "candidate")
    _require(
        candidate
        == {
            "release_version": RC32_VERSION,
            "git_commit": RC32_GIT_COMMIT,
            "image_digest": RC32_DIGEST,
        },
        "query-boundary candidate is not exact RC32",
    )
    _require(payload.get("stages") == QUERY_BOUNDARY_STAGES, "query-boundary stages drifted")
    provider = _mapping(payload.get("provider"), "provider")
    account = str(provider.get("account_id", ""))
    region = str(provider.get("region", ""))
    workgroup = str(provider.get("workgroup_name", ""))
    _require(account.isdigit() and len(account) == 12, "query-boundary account is not exact")
    _require(region == "us-east-1", "query-boundary region is not exact")
    _require(
        workgroup.startswith("dander-p8q-rc32-rs-query-c") and len(workgroup) <= 32,
        "query-boundary resource name is not bounded",
    )
    suffix = workgroup.rsplit("-", 1)[-1]
    _require(
        provider
        == {
            "account_id": account,
            "region": region,
            "workgroup_name": workgroup,
            "host": f"{workgroup}.{account}.{region}.redshift-serverless.amazonaws.com",
            "database": "analytics",
            "port": 5439,
        },
        "query-boundary provider binding drifted",
    )
    relative_objective = objective_path.resolve().relative_to(repository_root.resolve()).as_posix()
    harness_hash = (
        QUERY_BOUNDARY_C13_HARNESS_SHA256
        if relative_objective == QUERY_BOUNDARY_C13_OBJECTIVE
        else _sha256(repository_root / QUERY_BOUNDARY_SCRIPT)
    )
    execution = _mapping(payload.get("execution"), "execution")
    approval_reference = str(execution.get("approval_reference", ""))
    _require(
        execution
        == {
            "approval_reference": approval_reference,
            "harness_sha256": harness_hash,
            "maximum_manual_executions": 1,
            "automatic_retry": False,
            "provider_operation_retries": 0,
            "connect_timeout_seconds": 300,
            "ssl": True,
            "sslmode": "verify-full",
            "client_protocol_version": 0,
            "integrated_iam": False,
            "verified_postgres_tls_handshake": True,
            "comparison_driver": "psycopg",
            "redshift_connector_matches_current_product_configuration": True,
            "read_only_validation_query": "SELECT 1",
            "schema_or_workload_mutation_allowed": False,
        },
        "query-boundary execution is not exact",
    )
    _require(bool(approval_reference), "query-boundary approval reference is missing")
    _require(
        payload.get("diagnostic_boundaries")
        == {
            "psycopg_path_runs_before_redshift_connector_path": True,
            "connector_and_validation_query_stages_are_timed_separately": True,
            "sanitized_output_fields": ["stage", "elapsed_ms", "exception_class"],
            "provider_exception_messages_retained": False,
            "no_candidate_or_benchmark_command": True,
        },
        "query-boundary diagnostic contract drifted",
    )
    infrastructure = _mapping(payload.get("infrastructure"), "infrastructure")
    expected_members = [
        relative_objective,
        "scripts/__init__.py",
        "scripts/benchmarks/__init__.py",
        QUERY_BOUNDARY_SCRIPT,
    ]
    _require(
        infrastructure
        == {
            "data_plane": "infra/qualification/aws-native",
            "resource_name": workgroup,
            "state_key": (
                "dander/d7/control-plane/phase8-qualification/"
                f"rc32-redshift-query-boundary-{suffix}.tfstate"
            ),
            "staging_bucket": f"{workgroup}-{account}-staging",
            "redshift_serverless_base_capacity_rpu": 8,
            "redshift_serverless_daily_usage_limit_rpu_hours": 1,
            "postgresql_instance_class": "db.t4g.micro",
            "task_role_tag": "RedshiftDbRoles=dander_runtime",
            "task_role_global_actions": ["tag:GetResources", "tag:GetTagKeys"],
            "task_role_scoped_actions": [
                "redshift-serverless:GetCredentials",
                "s3:GetObject",
                "s3:PutObject",
            ],
            "task_cpu_units": 2048,
            "task_memory_mib": 4096,
            "task_cpu_architecture": "ARM64",
            "task_read_only_root": True,
            "task_user": "65532:65532",
            "task_tmpfs_path": "/tmp",
            "harness_working_directory": "/tmp/harness",
            "harness_environment": {"PYTHONPATH": "/tmp/harness"},
            "harness_bundle_key": (
                f"phase8/{RC32_VERSION}/staging/harness/"
                f"redshift-query-boundary-{harness_hash[:12]}-{suffix}.zip"
            ),
            "harness_bundle_contains": expected_members,
            "sanitized_output_key": (
                f"phase8/{RC32_VERSION}/staging/diagnostics/query-boundary.json"
            ),
            "task_timeout_seconds": 900,
            "maximum_state_machine_executions": 1,
            "state_machine_retry_states": 0,
            "ecs_task_retries": 0,
            "container_restarts": 0,
            "automatic_retry": False,
            "provider_operation_retries": 0,
            "schedule_created": False,
            "post_apply_no_drift_required": True,
        },
        "query-boundary infrastructure is not canonical",
    )
    for member in expected_members:
        _require((repository_root / member).is_file(), f"bundle member is missing: {member}")
    protection = _mapping(payload.get("protection"), "protection")
    protected_base = str(protection.get("protected_base_commit", ""))
    _require(
        len(protected_base) == 40
        and all(character in "0123456789abcdef" for character in protected_base),
        "query-boundary protected base is malformed",
    )
    _require(
        protection
        == {
            "protected_base_commit": protected_base,
            "exact_main_ci_required_before_execution": True,
            "immutable_image_reference": RC32_IMAGE,
            "image_manifest_architecture": "arm64",
            "image_manifest_digest": RC32_ARM64_MANIFEST_DIGEST,
            "image_tag_mutability": "IMMUTABLE",
        },
        "query-boundary protection drifted",
    )
    ceiling = _mapping(payload.get("cost_ceiling"), "cost_ceiling")
    _require(
        ceiling == {"amount_usd": "0.50", "approval_reference": approval_reference},
        "query-boundary cost ceiling drifted",
    )
    _validate_budget(payload)
    _require(
        payload.get("cleanup")
        == {
            "begin_immediately_after_terminal_diagnosis": True,
            "no_schema_copy_or_benchmark_mutation": True,
            "delete_exact_harness_and_sanitized_output": True,
            "deregister_exact_task_definition": True,
            "remove_exact_log_group_state_machine_roles_and_cluster": True,
            "destroy_launcher_before_data_plane": True,
            "terraform_state_entries_after_destroy": 0,
            "remote_state_versions_after_destroy": 0,
            "direct_owned_resource_inventories_empty": True,
        },
        "query-boundary cleanup drifted",
    )


def classify_ci_scope(paths: list[str]) -> str:
    """Return objective, benchmark, or full for one Git diff."""
    if not paths:
        return "full"
    lane = "objective"
    for value in paths:
        path = Path(value)
        light = (
            value == "AGENTS.md"
            or value.endswith("/AGENTS.md")
            or value == "HANDOFF.md"
            or (value.startswith("tickets/") and value.endswith(".md"))
            or value.startswith("docs/evidence/phase8/")
        )
        if light:
            if "objective" in path.name and not _is_rc32_redshift_objective(value):
                return "full"
            continue
        benchmark = (
            value.startswith("scripts/benchmarks/")
            or value == "scripts/validate_redshift_objective.py"
            or value == "tests/test_validate_redshift_objective.py"
            or (
                value.startswith("tests/portability/")
                and ("phase8" in path.name or "redshift" in path.name)
            )
        )
        if not benchmark:
            return "full"
        lane = "benchmark"
    return lane


def smoke_candidate_commands(
    paths: list[Path],
    *,
    repository_root: Path,
    image: str,
) -> None:
    """Help-smoke each objective's exact candidate command inside local RC32."""
    _require(_image_has_rc32_digest(image), "smoke image is not the immutable RC32 digest")
    for path in paths:
        payload = validate_objective(path, repository_root=repository_root)
        if payload.get("schema") == QUERY_BOUNDARY_SCHEMA:
            _smoke_query_boundary_diagnostic(path, payload, repository_root, image)
            continue
        configuration = _mapping(payload["configuration"], "configuration")
        module = str(_mapping(configuration["launcher_contract"], "contract")["benchmark_module"])
        fargate = _mapping(configuration["fargate_harness"], "fargate")
        members = [str(item) for item in _list(fargate["harness_bundle_contains"], "bundle")]
        _smoke_bundle(members, module, repository_root, image)


def _smoke_query_boundary_diagnostic(
    path: Path,
    payload: dict[str, object],
    repository_root: Path,
    image: str,
) -> None:
    infrastructure = _mapping(payload.get("infrastructure"), "infrastructure")
    members = [str(item) for item in _list(infrastructure.get("harness_bundle_contains"), "bundle")]
    with tempfile.TemporaryDirectory(prefix="dander-redshift-query-smoke-") as temporary:
        bundle = Path(temporary)
        for member in members:
            target = bundle / member
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository_root / member, target)
        relative_objective = path.resolve().relative_to(repository_root.resolve()).as_posix()
        shell = (
            "set -eu; "
            'case "$(command -v python)" in /app/.venv/bin/*) exit 91;; esac; '
            "cd /tmp/harness; export PYTHONPATH=/tmp/harness; "
            "python -c 'import psycopg, redshift_connector'; "
            "python -c 'import scripts.benchmarks.redshift_query_boundary_diagnostic_phase8'; "
            f"python {QUERY_BOUNDARY_SCRIPT} --help >/tmp/help.txt; "
            f"test -f {relative_objective}"
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/arm64",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=128m",
                "--mount",
                f"type=bind,source={bundle},target=/tmp/harness,readonly",
                "--user",
                "65532:65532",
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                shell,
            ],
            check=True,
        )


def smoke_benchmark_modules(
    modules: list[str],
    *,
    repository_root: Path,
    image: str,
) -> None:
    """Smoke generated commands for remaining modules before their objectives exist."""
    _require(bool(modules), "at least one benchmark module is required for image smoke")
    _require(_image_has_rc32_digest(image), "smoke image is not the immutable RC32 digest")
    for module in modules:
        script = _benchmark_script(module)
        members = [
            "scripts/__init__.py",
            "scripts/benchmarks/__init__.py",
            "scripts/benchmarks/redshift.py",
            LAUNCHER_PREFLIGHT,
            script,
            *BENCHMARK_DEPENDENCIES[module],
        ]
        _smoke_bundle(members, module, repository_root, image)


def _smoke_bundle(
    members: list[str],
    module: str,
    repository_root: Path,
    image: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="dander-redshift-smoke-") as temporary:
        bundle = Path(temporary)
        for member in members:
            target = bundle / member
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository_root / member, target)
        shell = (
            "set -eu; "
            'case "$(command -v python)" in /app/.venv/bin/*) exit 91;; esac; '
            'case "$(command -v dander)" in /app/.venv/bin/*) exit 92;; esac; '
            "cd /tmp/harness; export PYTHONPATH=/tmp/harness; "
            f"python {LAUNCHER_PREFLIGHT} --help >/tmp/preflight-help.txt; "
            f"python -c 'import {module}'; {_candidate_command(module)} --help >/tmp/help.txt"
        )
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/arm64",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=128m",
                "--mount",
                f"type=bind,source={bundle},target=/tmp/harness,readonly",
                "--user",
                "65532:65532",
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                shell,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        _require(result.returncode == 0, f"candidate command smoke exited {result.returncode}")


def _image_has_rc32_digest(image: str) -> bool:
    if image != RC32_IMAGE:
        return False
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            "{{json .RepoDigests}} {{.Architecture}}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return (
        result.returncode == 0 and f"@{RC32_DIGEST}" in result.stdout and " arm64" in result.stdout
    )


def _candidate_command(module: str) -> str:
    return (
        "cd /tmp/harness && PYTHONPATH=/tmp/harness dander qualification-run "
        + _benchmark_script(module)
    )


def _benchmark_script(module: str) -> str:
    _require(module in BENCHMARK_DEPENDENCIES, f"unsupported benchmark module: {module}")
    return module.replace(".", "/") + ".py"


def _is_rc32_redshift_objective(path: str) -> bool:
    name = Path(path).name
    return "rc32-redshift" in name and "objective" in name and name.endswith(".json")


def _load_object(path: Path) -> dict[str, object]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ObjectiveValidationError(f"could not load {path}: {error}") from error
    return _mapping(payload, str(path))


def _at(payload: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = payload
    for field in path:
        value = _mapping(value, ".".join(path)).get(field)
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ObjectiveValidationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ObjectiveValidationError(f"{label} must be a list")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ObjectiveValidationError(f"{label} must be a decimal") from error


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ObjectiveValidationError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("objectives", nargs="*", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--smoke-image")
    parser.add_argument(
        "--smoke-module",
        action="append",
        choices=sorted(BENCHMARK_DEPENDENCIES),
        default=[],
    )
    parser.add_argument("--print-contract", choices=sorted(BENCHMARK_DEPENDENCIES))
    parser.add_argument("--ci-scope", action="store_true")
    arguments = parser.parse_args()

    if arguments.print_contract:
        print(json.dumps(canonical_launcher_contract(arguments.print_contract), indent=2))
        return
    if arguments.ci_scope:
        print(classify_ci_scope([str(path) for path in arguments.objectives]))
        return

    root = arguments.repository_root.resolve()
    paths = [path if path.is_absolute() else root / path for path in arguments.objectives]
    try:
        if paths:
            validate_objectives(paths, repository_root=root)
        if arguments.smoke_image and paths:
            smoke_candidate_commands(paths, repository_root=root, image=arguments.smoke_image)
        if arguments.smoke_module:
            _require(bool(arguments.smoke_image), "--smoke-module requires --smoke-image")
            smoke_benchmark_modules(
                arguments.smoke_module,
                repository_root=root,
                image=arguments.smoke_image,
            )
        _require(bool(paths or arguments.smoke_module), "objective or smoke module required")
    except ObjectiveValidationError as error:
        raise SystemExit(f"RC32 Redshift objective rejected: {error}") from error
    smoke_count = len(arguments.smoke_module)
    if arguments.smoke_image:
        smoke_count += len(paths)
    print(f"Validated {len(paths)} objective(s); smoked {smoke_count} generated command(s).")


if __name__ == "__main__":
    main()
