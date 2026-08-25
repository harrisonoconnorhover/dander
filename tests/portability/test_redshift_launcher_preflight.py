"""Credential-free coverage for the AWS-native Redshift launcher preflight."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import redshift_launcher_preflight as preflight

if TYPE_CHECKING:
    from pathlib import Path


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.upload: dict[str, object] | None = None

    def get_caller_identity(self, **kwargs: object) -> object:
        self.calls.append(("identity", kwargs))
        return {"Account": "123456789012"}

    def get_resources(self, **kwargs: object) -> object:
        self.calls.append(("resources", kwargs))
        return {"ResourceTagMappingList": []}

    def get_tag_keys(self, **kwargs: object) -> object:
        self.calls.append(("tag_keys", kwargs))
        return {"TagKeys": []}

    def get_bucket_location(self, **kwargs: object) -> object:
        self.calls.append(("bucket_location", kwargs))
        return {"LocationConstraint": None}

    def head_object(self, **kwargs: object) -> object:
        self.calls.append(("head", kwargs))
        return {}

    def put_object(self, **kwargs: object) -> object:
        self.upload = kwargs
        return {}

    def get_credentials(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("credentials", kwargs))
        return {"dbUser": "temporary-user", "dbPassword": "temporary-secret"}


class _Cursor:
    def __init__(self) -> None:
        self.statement: str | None = None
        self.closed = False

    def execute(self, operation: str) -> object:
        self.statement = operation
        return None

    def fetchone(self) -> object:
        return (1,)

    def close(self) -> object:
        self.closed = True
        return None


class _Connection:
    def __init__(self) -> None:
        self.selected = _Cursor()
        self.closed = False

    def cursor(self) -> _Cursor:
        return self.selected

    def close(self) -> object:
        self.closed = True
        return None


class _TimeoutCursor(_Cursor):
    def execute(self, operation: str) -> object:
        raise TimeoutError


class _TimeoutConnection(_Connection):
    def __init__(self) -> None:
        super().__init__()
        self.selected = _TimeoutCursor()


class SensitiveConnectionError(RuntimeError):
    """A test-only provider failure whose message must never reach evidence."""


def _config(tmp_path: Path) -> preflight.LauncherPreflightConfig:
    return preflight.LauncherPreflightConfig(
        objective_path=tmp_path / "objective.json",
        account_id="123456789012",
        region="us-east-1",
        host=(
            "dander-p8q-rc32-rs-fail-c12.123456789012.us-east-1.redshift-serverless.amazonaws.com"
        ),
        database="analytics",
        workgroup_name="dander-p8q-rc32-rs-fail-c12",
        staging_bucket="dander-p8q-rc32-rs-fail-c12-123456789012-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
        harness_bundle_key="phase8/0.9.0rc32/staging/harness/bundle.zip",
        diagnostics_key="phase8/0.9.0rc32/staging/diagnostics/launcher-preflight.json",
        benchmark_module="scripts.benchmarks.redshift_failure_phase8",
        bundle_members=("scripts/benchmarks/redshift_failure_phase8.py",),
        expected_hashes=(),
        readiness_socket_timeout_seconds=12,
        readiness_maximum_probes=4,
        readiness_probe_interval_seconds=5,
        readiness_window_seconds=115,
    )


def _clients(client: _Client) -> preflight._Clients:  # noqa: SLF001
    return preflight._Clients(  # noqa: SLF001
        sts=client,
        tagging=client,
        s3=client,
        serverless=client,
    )


def test_preflight_uses_explicit_tls_credentials_and_select_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    client = _Client()
    connection = _Connection()
    connector_calls: list[dict[str, object]] = []

    def connect(**kwargs: object) -> object:
        connector_calls.append(kwargs)
        return connection

    stages = preflight.run_preflight(
        _config(tmp_path),
        clients=_clients(client),
        connector=connect,
        environment_check=lambda _config: None,
    )

    assert [stage["stage"] for stage in stages] == [
        "launcher_environment",
        "iam_readiness",
        "get_credentials",
        "readiness_connector_1",
        "readiness_validation_query_1",
    ]
    assert all(stage["exception_class"] is None for stage in stages)
    assert ("bucket_location", {"Bucket": _config(tmp_path).staging_bucket}) in client.calls
    assert connector_calls == [
        {
            "user": "temporary-user",
            "password": "temporary-secret",
            "iam": False,
            "ssl": True,
            "sslmode": "verify-full",
            "host": _config(tmp_path).host,
            "port": 5439,
            "database": "analytics",
            "region": "us-east-1",
            "timeout": 12,
            "application_name": "dander-phase8-readiness-1",
            "client_protocol_version": 0,
            "is_serverless": True,
            "serverless_work_group": "dander-p8q-rc32-rs-fail-c12",
        }
    ]
    assert connection.selected.statement == "SELECT 1"
    assert connection.selected.closed and connection.closed


def test_failed_stage_is_sanitized_and_published_to_the_exact_owned_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    client = _Client()
    config = _config(tmp_path)

    def fail_connect(**_kwargs: object) -> object:
        raise SensitiveConnectionError("credential=must-not-leak")

    stages = preflight.run_preflight(
        config,
        clients=_clients(client),
        connector=fail_connect,
        environment_check=lambda _config: None,
        sleeper=lambda _seconds: None,
    )
    preflight.publish_preflight(config, stages, s3_client=client)

    assert stages[-1]["stage"] == "readiness_connector_4"
    assert stages[-1]["exception_class"] == "SensitiveConnectionError"
    assert client.upload is not None
    assert client.upload["Bucket"] == config.staging_bucket
    assert client.upload["Key"] == config.diagnostics_key
    assert client.upload["ServerSideEncryption"] == "AES256"
    body = cast("bytes", client.upload["Body"]).decode()
    assert "credential=must-not-leak" not in body
    assert "temporary-secret" not in body
    payload = json.loads(body)
    assert payload["schema"] == "io.dander.phase8.aws-native-redshift-launcher-preflight/v1"
    assert set(payload) == {"schema", "stages"}
    assert all(
        set(stage) == {"stage", "elapsed_ms", "exception_class"} for stage in payload["stages"]
    )


def test_readiness_query_timeout_can_recover_without_becoming_a_workload_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    client = _Client()
    timed_out = _TimeoutConnection()
    connection = _Connection()
    attempts = 0

    def connect(**_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return timed_out
        return connection

    stages = preflight.run_preflight(
        _config(tmp_path),
        clients=_clients(client),
        connector=connect,
        environment_check=lambda _config: None,
        sleeper=lambda _seconds: None,
    )

    assert [stage["stage"] for stage in stages[-4:]] == [
        "readiness_connector_1",
        "readiness_validation_query_1",
        "readiness_connector_2",
        "readiness_validation_query_2",
    ]
    assert preflight._preflight_passed(stages)  # noqa: SLF001
    assert attempts == 2
    assert timed_out.selected.closed and timed_out.closed


def test_preflight_rejects_provider_retries_before_any_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    with pytest.raises(ValueError, match="one standard AWS attempt"):
        preflight.run_preflight(
            _config(tmp_path),
            clients=_clients(_Client()),
            environment_check=lambda _config: None,
        )


def test_main_sanitizes_configuration_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    objective = tmp_path / "objective.json"
    objective.write_text("{credential=must-not-leak}", encoding="utf-8")

    assert preflight.main(["--objective", str(objective)]) == 3

    output = capsys.readouterr()
    assert not output.err
    assert "credential=must-not-leak" not in output.out
    payload = json.loads(output.out)
    assert set(payload) == {"stage", "elapsed_ms", "exception_class"}
    assert payload["stage"] == "launcher_configuration"
    assert payload["exception_class"] == "JSONDecodeError"
