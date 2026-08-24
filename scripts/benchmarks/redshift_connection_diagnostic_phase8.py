#!/usr/bin/env python3
"""Sanitized Redshift Serverless connection-boundary diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from dander import __version__
from dander.providers.redshift import runtime as redshift_runtime
from dander.providers.redshift.config import RedshiftWarehouseConfig
from dander.providers.redshift.session import execute

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from dander.providers.redshift.session import RedshiftConnection


_APPROVAL_SCHEMA = "io.dander.phase8.redshift-connection-diagnostic-approval/v3"
_DIAGNOSTIC_STAGES = (
    "get_credentials",
    "explicit_credentials_connector",
    "explicit_credentials_validation_query",
    "dander_current_connector",
    "dander_current_validation_query",
)
_MAXIMUM_MANUAL_EXECUTIONS = 20


class _RedshiftServerlessClient(Protocol):
    def get_credentials(self, **kwargs: object) -> Mapping[str, object]: ...


class _Connection(Protocol):
    def close(self) -> object: ...


class DiagnosticPrerequisiteError(RuntimeError):
    """Indicate that an earlier sanitized diagnostic stage did not complete."""


@dataclass(frozen=True, slots=True)
class DiagnosticConfig:
    """Exact disposable provider coordinates without credential material."""

    account_id: str
    host: str
    database: str
    region: str
    workgroup_name: str
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str
    port: int = 5439
    connect_timeout_seconds: int = 300

    def warehouse_config(self) -> RedshiftWarehouseConfig:
        return RedshiftWarehouseConfig(
            provider="redshift",
            deployment="serverless",
            host=self.host,
            port=self.port,
            database=self.database,
            schema_name="diagnostic_unused",
            region=self.region,
            workgroup_name=self.workgroup_name,
            database_role="dander_runtime",
            copy_role_arn=self.copy_role_arn,
            staging_bucket=self.staging_bucket,
            staging_prefix=self.staging_prefix,
            connect_timeout_seconds=self.connect_timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    release_version: str
    git_commit: str
    image_digest: str
    approval_reference: str


def run_diagnostic(
    config: DiagnosticConfig,
    *,
    serverless_client: _RedshiftServerlessClient | None = None,
    connector: Callable[..., object] | None = None,
) -> dict[str, object]:
    """Compare explicit temporary credentials with Dander's current factory."""
    _require_no_provider_retries()
    stages: list[dict[str, object]] = []
    client = serverless_client or _serverless_client(config.region)
    explicit_connector = connector or _connector()
    credentials = _record_stage(
        stages,
        "get_credentials",
        lambda: client.get_credentials(
            workgroupName=config.workgroup_name,
            dbName=config.database,
            durationSeconds=900,
        ),
    )

    def connect_explicitly() -> object:
        if not isinstance(credentials, Mapping):
            raise DiagnosticPrerequisiteError
        username = credentials.get("dbUser")
        password = credentials.get("dbPassword")
        if not isinstance(username, str) or not username:
            raise DiagnosticPrerequisiteError
        if not isinstance(password, str) or not password:
            raise DiagnosticPrerequisiteError
        return explicit_connector(
            user=username,
            password=password,
            iam=False,
            ssl=True,
            sslmode="verify-full",
            host=config.host,
            port=config.port,
            database=config.database,
            region=config.region,
            timeout=config.connect_timeout_seconds,
            application_name="dander",
            client_protocol_version=0,
            is_serverless=True,
            serverless_work_group=config.workgroup_name,
        )

    explicit_connection = _record_stage(
        stages,
        "explicit_credentials_connector",
        connect_explicitly,
    )
    try:
        _record_stage(
            stages,
            "explicit_credentials_validation_query",
            lambda: _validate_connection(explicit_connection, config.database),
        )
    finally:
        _close_connection(explicit_connection)

    dander_connection = _record_stage(
        stages,
        "dander_current_connector",
        redshift_runtime._sdk_connection_factory(config.warehouse_config()),  # noqa: SLF001
    )
    try:
        _record_stage(
            stages,
            "dander_current_validation_query",
            lambda: _validate_connection(dander_connection, config.database),
        )
    finally:
        _close_connection(dander_connection)

    return {"stages": stages}


def _validate_connection(connection: object | None, database: str) -> None:
    if connection is None:
        raise DiagnosticPrerequisiteError
    current = execute(
        cast("RedshiftConnection", connection),
        "SELECT current_database(), current_user",
        fetch="one",
    ).row
    if (
        not isinstance(current, (tuple, list))
        or len(current) < 2
        or current[0] != database
        or not isinstance(current[1], str)
        or not current[1]
    ):
        raise DiagnosticPrerequisiteError


def _close_connection(connection: object | None) -> None:
    if connection is not None:
        cast("_Connection", connection).close()


def _record_stage(
    stages: list[dict[str, object]],
    name: str,
    operation: Callable[[], object],
) -> object | None:
    started = time.perf_counter()
    exception_class: str | None = None
    result: object | None = None
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
    return result


def _load_approval(
    path: Path,
    *,
    config: DiagnosticConfig,
    identity: CandidateIdentity,
    execution_number: int,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("diagnostic approval schema is incompatible")
    if payload.get("stages") != list(_DIAGNOSTIC_STAGES):
        raise ValueError("diagnostic approval changed the connection stages")
    candidate = _mapping(payload.get("candidate"), "candidate")
    expected_candidate = {
        "release_version": identity.release_version,
        "git_commit": identity.git_commit,
        "image_digest": identity.image_digest,
    }
    if candidate != expected_candidate:
        raise ValueError("diagnostic approval does not match the candidate")
    provider = _mapping(payload.get("provider"), "provider")
    expected_provider = {
        "account_id": config.account_id,
        "region": config.region,
        "workgroup_name": config.workgroup_name,
        "host": config.host,
        "database": config.database,
        "port": config.port,
    }
    if provider != expected_provider:
        raise ValueError("diagnostic approval does not match the provider boundary")
    execution = _mapping(payload.get("execution"), "execution")
    expected_execution = {
        "approval_reference": identity.approval_reference,
        "harness_sha256": _file_sha256(Path(__file__)),
        "maximum_manual_executions": _MAXIMUM_MANUAL_EXECUTIONS,
        "automatic_retry": False,
        "provider_operation_retries": 0,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "ssl": True,
        "sslmode": "verify-full",
        "client_protocol_version": 0,
        "integrated_iam_for_explicit_credentials": False,
        "current_dander_connection_factory": True,
        "read_only_validation_query": "SELECT current_database(), current_user",
        "schema_or_workload_mutation_allowed": False,
    }
    if execution != expected_execution:
        raise ValueError("diagnostic approval does not match the protected execution")
    if execution_number < 1 or execution_number > _MAXIMUM_MANUAL_EXECUTIONS:
        raise ValueError("diagnostic execution number exceeds the protected maximum")


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise ValueError("AWS_MAX_ATTEMPTS must allow exactly one provider attempt")
    if os.environ.get("AWS_RETRY_MODE") != "standard":
        raise ValueError("AWS_RETRY_MODE must be standard")


def _serverless_client(region: str) -> _RedshiftServerlessClient:
    boto3 = importlib.import_module("boto3")
    client = cast("Callable[..., object]", boto3.client)("redshift-serverless", region_name=region)
    return cast("_RedshiftServerlessClient", client)


def _connector() -> Callable[..., object]:
    module = importlib.import_module("redshift_connector")
    return cast("Callable[..., object]", module.connect)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-manifest", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--workgroup-name", required=True)
    parser.add_argument("--copy-role-arn", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--staging-prefix", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--execution-number", type=int, required=True)
    parser.add_argument("--port", type=int, default=5439)
    parser.add_argument("--connect-timeout-seconds", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = DiagnosticConfig(
        account_id=arguments.account_id,
        host=arguments.host,
        database=arguments.database,
        region=arguments.region,
        workgroup_name=arguments.workgroup_name,
        copy_role_arn=arguments.copy_role_arn,
        staging_bucket=arguments.staging_bucket,
        staging_prefix=arguments.staging_prefix,
        port=arguments.port,
        connect_timeout_seconds=arguments.connect_timeout_seconds,
    )
    identity = CandidateIdentity(
        release_version=arguments.release_version,
        git_commit=arguments.git_commit,
        image_digest=arguments.image_digest,
        approval_reference=arguments.approval_reference,
    )
    if __version__ != identity.release_version:
        raise ValueError("installed Dander version does not match the diagnostic candidate")
    _load_approval(
        arguments.approval_manifest,
        config=config,
        identity=identity,
        execution_number=arguments.execution_number,
    )
    print(json.dumps(run_diagnostic(config), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
