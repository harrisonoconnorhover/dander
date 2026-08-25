#!/usr/bin/env python3
"""Fail-closed AWS-native Redshift launcher preflight with sanitized S3 evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


_ARTIFACT_SCHEMA = "io.dander.phase8.aws-native-redshift-launcher-preflight/v1"
_ALLOWED_FIELDS = ("stage", "elapsed_ms", "exception_class")
_PREFLIGHT_EXIT = 3
_READINESS_SOCKET_TIMEOUT_SECONDS = 12
_READINESS_MAXIMUM_PROBES = 4
_READINESS_PROBE_INTERVAL_SECONDS = 5
_READINESS_WINDOW_SECONDS = 115


class _Client(Protocol):
    def get_caller_identity(self, **kwargs: object) -> object: ...
    def get_resources(self, **kwargs: object) -> object: ...
    def get_tag_keys(self, **kwargs: object) -> object: ...
    def get_bucket_location(self, **kwargs: object) -> object: ...
    def head_object(self, **kwargs: object) -> object: ...
    def put_object(self, **kwargs: object) -> object: ...
    def get_credentials(self, **kwargs: object) -> Mapping[str, object]: ...


class _Cursor(Protocol):
    def execute(self, operation: str) -> object: ...
    def fetchone(self) -> object: ...
    def close(self) -> object: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...
    def close(self) -> object: ...


class LauncherPreflightError(RuntimeError):
    """Raised internally without retaining provider exception messages."""


@dataclass(frozen=True, slots=True)
class LauncherPreflightConfig:
    """Exact non-secret inputs bound by one protected objective."""

    objective_path: Path
    account_id: str
    region: str
    host: str
    database: str
    workgroup_name: str
    staging_bucket: str
    staging_prefix: str
    harness_bundle_key: str
    diagnostics_key: str
    benchmark_module: str
    bundle_members: tuple[str, ...]
    expected_hashes: tuple[tuple[str, str], ...]
    readiness_socket_timeout_seconds: int
    readiness_maximum_probes: int
    readiness_probe_interval_seconds: int
    readiness_window_seconds: int


@dataclass(frozen=True, slots=True)
class _Clients:
    sts: _Client
    tagging: _Client
    s3: _Client
    serverless: _Client


def load_config(path: Path) -> LauncherPreflightConfig:
    """Load the exact launcher values already reviewed in the objective."""
    payload = _mapping(json.loads(path.read_text(encoding="utf-8")), "objective")
    configuration = _mapping(payload.get("configuration"), "configuration")
    contract = _mapping(configuration.get("launcher_contract"), "launcher contract")
    connection = _mapping(contract.get("connection"), "connection")
    redshift = _mapping(configuration.get("redshift"), "redshift")
    fargate = _mapping(configuration.get("fargate_harness"), "Fargate harness")
    execution = _mapping(configuration.get("execution"), "execution")
    members = _strings(fargate.get("harness_bundle_contains"), "bundle members")
    expected_hashes: tuple[tuple[str, str], ...] = (
        (str(contract.get("benchmark_module", "")).replace(".", "/") + ".py", "harness_sha256"),
        ("scripts/benchmarks/redshift.py", "shared_harness_sha256"),
        ("scripts/benchmarks/redshift_launcher_preflight.py", "launcher_preflight_sha256"),
    )
    if "bulk_harness_sha256" in execution:
        expected_hashes += (("scripts/benchmarks/redshift_bulk_phase8.py", "bulk_harness_sha256"),)
    hashes = tuple((member, str(execution.get(field, ""))) for member, field in expected_hashes)
    readiness_fields = {
        "readiness_socket_timeout_seconds": connection.get("readiness_socket_timeout_seconds"),
        "readiness_maximum_probes": connection.get("readiness_maximum_probes"),
        "readiness_probe_interval_seconds": connection.get("readiness_probe_interval_seconds"),
        "readiness_window_seconds": connection.get("readiness_window_seconds"),
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) for value in readiness_fields.values()
    ):
        raise ValueError("launcher preflight readiness configuration is malformed")
    config = LauncherPreflightConfig(
        objective_path=path,
        account_id=str(redshift.get("account_id", "")),
        region=str(redshift.get("region", "")),
        host=str(redshift.get("host", "")),
        database=str(redshift.get("database", "")),
        workgroup_name=str(redshift.get("workgroup_name", "")),
        staging_bucket=str(redshift.get("staging_bucket", "")),
        staging_prefix=str(redshift.get("staging_prefix", "")).strip("/"),
        harness_bundle_key=str(fargate.get("harness_bundle_key", "")),
        diagnostics_key=str(fargate.get("transient_launcher_preflight_key", "")),
        benchmark_module=str(contract.get("benchmark_module", "")),
        bundle_members=members,
        expected_hashes=hashes,
        readiness_socket_timeout_seconds=cast(
            "int", readiness_fields["readiness_socket_timeout_seconds"]
        ),
        readiness_maximum_probes=cast("int", readiness_fields["readiness_maximum_probes"]),
        readiness_probe_interval_seconds=cast(
            "int", readiness_fields["readiness_probe_interval_seconds"]
        ),
        readiness_window_seconds=cast("int", readiness_fields["readiness_window_seconds"]),
    )
    _validate_config(config)
    return config


def run_preflight(
    config: LauncherPreflightConfig,
    *,
    clients: _Clients | None = None,
    connector: Callable[..., object] | None = None,
    environment_check: Callable[[LauncherPreflightConfig], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], ...]:
    """Run bounded read-only readiness probes before the candidate command."""
    _require_no_provider_retries()
    selected = clients or _clients(config.region)
    stages: list[dict[str, object]] = []
    check_environment = environment_check or _check_environment
    if not _record(stages, "launcher_environment", lambda: check_environment(config))[1]:
        return tuple(stages)
    if not _record(stages, "iam_readiness", lambda: _check_iam(config, selected))[1]:
        return tuple(stages)
    credentials, ok = _record(
        stages,
        "get_credentials",
        lambda: selected.serverless.get_credentials(
            workgroupName=config.workgroup_name,
            dbName=config.database,
            durationSeconds=900,
        ),
    )
    if not ok:
        return tuple(stages)
    selected_connector = connector or _connector()
    deadline = time.monotonic() + config.readiness_window_seconds
    for probe in range(1, config.readiness_maximum_probes + 1):
        if deadline - time.monotonic() < config.readiness_socket_timeout_seconds * 2:
            break
        connection, connected = _record(
            stages,
            f"readiness_connector_{probe}",
            partial(_connect, config, credentials, selected_connector, probe=probe),
        )
        query_passed = False
        if connected:
            try:
                _, query_passed = _record(
                    stages,
                    f"readiness_validation_query_{probe}",
                    partial(_select_one, connection),
                )
            finally:
                cast("_Connection", connection).close()
        if query_passed:
            return tuple(stages)
        if probe < config.readiness_maximum_probes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleeper(min(config.readiness_probe_interval_seconds, remaining))
    return tuple(stages)


def publish_preflight(
    config: LauncherPreflightConfig,
    stages: tuple[dict[str, object], ...],
    *,
    s3_client: _Client,
) -> None:
    """Persist only the sanitized stage contract to the exact owned key."""
    body = json.dumps(
        {"schema": _ARTIFACT_SCHEMA, "stages": stages},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    s3_client.put_object(
        Bucket=config.staging_bucket,
        Key=config.diagnostics_key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )


def _validate_config(config: LauncherPreflightConfig) -> None:
    expected_host = (
        f"{config.workgroup_name}.{config.account_id}.{config.region}."
        "redshift-serverless.amazonaws.com"
    )
    expected_diagnostics = f"{config.staging_prefix}/diagnostics/launcher-preflight.json"
    if config.host != expected_host:
        raise ValueError("Redshift host is not the exact owned Serverless workgroup")
    if config.diagnostics_key != expected_diagnostics:
        raise ValueError("launcher preflight artifact key is not exact")
    if (
        config.readiness_socket_timeout_seconds != _READINESS_SOCKET_TIMEOUT_SECONDS
        or config.readiness_maximum_probes != _READINESS_MAXIMUM_PROBES
        or config.readiness_probe_interval_seconds != _READINESS_PROBE_INTERVAL_SECONDS
        or config.readiness_window_seconds != _READINESS_WINDOW_SECONDS
    ):
        raise ValueError("launcher preflight readiness configuration is not exact")
    if not config.database or not config.harness_bundle_key or not config.benchmark_module:
        raise ValueError("launcher preflight configuration is incomplete")


def _check_environment(config: LauncherPreflightConfig) -> None:
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        raise LauncherPreflightError
    if Path.cwd().resolve() != Path("/tmp/harness"):
        raise LauncherPreflightError
    if os.environ.get("PYTHONPATH") != "/tmp/harness" or os.geteuid() == 0:
        raise LauncherPreflightError
    if not os.statvfs("/").f_flag & os.ST_RDONLY:
        raise LauncherPreflightError
    for executable in ("python", "dander"):
        resolved = shutil.which(executable)
        if not resolved or resolved.startswith("/app/.venv/bin/"):
            raise LauncherPreflightError
    with tempfile.NamedTemporaryFile(dir="/tmp"):
        pass
    root = Path("/tmp/harness")
    for member in config.bundle_members:
        target = (root / member).resolve()
        if root not in target.parents or not target.is_file():
            raise LauncherPreflightError
    for member, expected in config.expected_hashes:
        if not expected or _sha256(root / member) != expected:
            raise LauncherPreflightError
    importlib.import_module(config.benchmark_module)


def _check_iam(config: LauncherPreflightConfig, clients: _Clients) -> None:
    clients.sts.get_caller_identity()
    clients.tagging.get_resources(ResourcesPerPage=1)
    clients.tagging.get_tag_keys()
    clients.s3.get_bucket_location(Bucket=config.staging_bucket)
    clients.s3.head_object(Bucket=config.staging_bucket, Key=config.harness_bundle_key)


def _connect(
    config: LauncherPreflightConfig,
    credentials: object,
    connector: Callable[..., object],
    *,
    probe: int,
) -> object:
    values = _mapping(credentials, "credentials")
    username = values.get("dbUser")
    password = values.get("dbPassword")
    if (
        not isinstance(username, str)
        or not username
        or not isinstance(password, str)
        or not password
    ):
        raise LauncherPreflightError
    return connector(
        user=username,
        password=password,
        iam=False,
        ssl=True,
        sslmode="verify-full",
        host=config.host,
        port=5439,
        database=config.database,
        region=config.region,
        timeout=config.readiness_socket_timeout_seconds,
        application_name=f"dander-phase8-readiness-{probe}",
        client_protocol_version=0,
        is_serverless=True,
        serverless_work_group=config.workgroup_name,
    )


def _select_one(connection: object) -> None:
    cursor = cast("_Connection", connection).cursor()
    try:
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        if not isinstance(row, (tuple, list)) or not row or row[0] != 1:
            raise LauncherPreflightError
    finally:
        cursor.close()


def _record(
    stages: list[dict[str, object]],
    name: str,
    operation: Callable[[], object],
) -> tuple[object | None, bool]:
    started = time.perf_counter()
    result: object | None = None
    exception_class: str | None = None
    try:
        result = operation()
    except Exception as error:
        exception_class = type(error).__name__
    stages.append(
        {
            "stage": name,
            "elapsed_ms": max(0, round((time.perf_counter() - started) * 1_000)),
            "exception_class": exception_class,
        }
    )
    return result, exception_class is None


def _clients(region: str) -> _Clients:
    boto3 = importlib.import_module("boto3")
    client = cast("Callable[..., object]", boto3.client)
    return _Clients(
        sts=cast("_Client", client("sts", region_name=region)),
        tagging=cast("_Client", client("resourcegroupstaggingapi", region_name=region)),
        s3=cast("_Client", client("s3", region_name=region)),
        serverless=cast("_Client", client("redshift-serverless", region_name=region)),
    )


def _connector() -> Callable[..., object]:
    return cast("Callable[..., object]", importlib.import_module("redshift_connector").connect)


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1" or os.environ.get("AWS_RETRY_MODE") != "standard":
        raise ValueError("launcher preflight requires one standard AWS attempt")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast("dict[str, object]", value)


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objective", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    started = time.perf_counter()
    try:
        config = load_config(arguments.objective)
    except Exception as error:
        _print_stages((_failure_stage("launcher_configuration", started, error),))
        return _PREFLIGHT_EXIT
    try:
        selected = _clients(config.region)
    except Exception as error:
        _print_stages((_failure_stage("launcher_client_initialization", started, error),))
        return _PREFLIGHT_EXIT
    try:
        stages = run_preflight(config, clients=selected)
    except Exception as error:
        stages = (_failure_stage("launcher_preflight_internal", started, error),)
    try:
        publish_preflight(config, stages, s3_client=selected.s3)
    except Exception as error:
        stages += (_failure_stage("diagnostic_artifact_upload", time.perf_counter(), error),)
    _print_stages(stages)
    passed = _preflight_passed(stages)
    return 0 if passed else _PREFLIGHT_EXIT


def _preflight_passed(stages: tuple[dict[str, object], ...]) -> bool:
    if not stages:
        return False
    terminal = stages[-1]
    return bool(
        str(terminal.get("stage", "")).startswith("readiness_validation_query_")
        and terminal.get("exception_class") is None
    )


def _failure_stage(name: str, started: float, error: Exception) -> dict[str, object]:
    return {
        "stage": name,
        "elapsed_ms": max(0, round((time.perf_counter() - started) * 1_000)),
        "exception_class": type(error).__name__,
    }


def _print_stages(stages: tuple[dict[str, object], ...]) -> None:
    for stage in stages:
        print(json.dumps({field: stage[field] for field in _ALLOWED_FIELDS}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
