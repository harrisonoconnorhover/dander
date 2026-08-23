"""Credential-free checks for the Redshift connection-boundary diagnostic."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import redshift_connection_diagnostic_phase8 as diagnostic

from dander import __version__

if TYPE_CHECKING:
    from collections.abc import Mapping


_COMMIT = "a" * 40
_DIGEST = f"sha256:{'b' * 64}"
_REFERENCE = "codex-goal-redshift-connection-diagnostic"


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ServerlessClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def get_credentials(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return {"dbUser": "temporary-user", "dbPassword": "temporary-password"}


def _config() -> diagnostic.DiagnosticConfig:
    return diagnostic.DiagnosticConfig(
        account_id="123456789012",
        host="workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc31-rs-connect-diag",
        copy_role_arn="arn:aws:iam::123456789012:role/dander-redshift-copy",
        staging_bucket="dander-redshift-staging",
        staging_prefix="phase8/0.9.0rc31/staging",
    )


def _identity() -> diagnostic.CandidateIdentity:
    return diagnostic.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
    )


def _install_connector(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, object]],
    *,
    fail_explicit: Exception | None = None,
    fail_iam: Exception | None = None,
) -> None:
    def connect(**kwargs: object) -> object:
        calls.append(kwargs)
        failure = fail_iam if kwargs.get("iam") is True else fail_explicit
        if failure is not None:
            raise failure
        return _Connection()

    module = ModuleType("redshift_connector")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redshift_connector", module)


def test_diagnostic_compares_explicit_credentials_with_current_dander_iam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    client = _ServerlessClient()
    calls: list[dict[str, object]] = []
    _install_connector(monkeypatch, calls)

    result = diagnostic.run_diagnostic(_config(), serverless_client=client)

    assert [stage["stage"] for stage in cast("list[dict[str, object]]", result["stages"])] == [
        "get_credentials",
        "explicit_credentials_connector",
        "dander_iam_connector",
    ]
    assert all(
        stage["exception_class"] is None
        for stage in cast("list[dict[str, object]]", result["stages"])
    )
    assert client.calls == [
        {
            "workgroupName": "dander-p8q-rc31-rs-connect-diag",
            "dbName": "analytics",
            "durationSeconds": 900,
        }
    ]
    assert len(calls) == 2
    explicit, current = calls
    assert explicit["iam"] is False
    assert explicit["user"] == "temporary-user"
    assert explicit["password"] == "temporary-password"
    assert current["iam"] is True
    assert "user" not in current and "password" not in current
    for call in calls:
        assert call["ssl"] is True
        assert call["sslmode"] == "verify-full"
        assert call["timeout"] == 300
        assert call["client_protocol_version"] == 0


def test_diagnostic_emits_only_sanitized_stage_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    calls: list[dict[str, object]] = []
    _install_connector(
        monkeypatch,
        calls,
        fail_explicit=TimeoutError("temporary-password private endpoint"),
        fail_iam=RuntimeError("private AWS response"),
    )

    result = diagnostic.run_diagnostic(_config(), serverless_client=_ServerlessClient())
    encoded = json.dumps(result)
    stages = cast("list[dict[str, object]]", result["stages"])

    assert [stage["exception_class"] for stage in stages] == [
        None,
        "TimeoutError",
        "RuntimeError",
    ]
    assert all(set(stage) == {"stage", "elapsed_ms", "exception_class"} for stage in stages)
    assert "temporary-password" not in encoded
    assert "private endpoint" not in encoded
    assert "private AWS response" not in encoded


def test_diagnostic_records_missing_credentials_and_still_compares_current_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    calls: list[dict[str, object]] = []
    _install_connector(monkeypatch, calls)

    result = diagnostic.run_diagnostic(
        _config(),
        serverless_client=_ServerlessClient(failure=PermissionError("private policy")),
    )
    stages = cast("list[dict[str, object]]", result["stages"])

    assert [stage["exception_class"] for stage in stages] == [
        "PermissionError",
        "DiagnosticPrerequisiteError",
        None,
    ]
    assert len(calls) == 1
    assert calls[0]["iam"] is True


def test_approval_binds_tls_timeout_protocol_and_exact_harness(tmp_path: Path) -> None:
    config = _config()
    identity = _identity()
    payload = {
        "schema": diagnostic._APPROVAL_SCHEMA,
        "stages": list(diagnostic._DIAGNOSTIC_STAGES),
        "candidate": {
            "release_version": identity.release_version,
            "git_commit": identity.git_commit,
            "image_digest": identity.image_digest,
        },
        "provider": {
            "account_id": config.account_id,
            "region": config.region,
            "workgroup_name": config.workgroup_name,
            "host": config.host,
            "database": config.database,
            "port": config.port,
        },
        "execution": {
            "approval_reference": identity.approval_reference,
            "harness_sha256": diagnostic._file_sha256(Path(diagnostic.__file__)),
            "manual_executions": 1,
            "automatic_retry": False,
            "provider_operation_retries": 0,
            "connect_timeout_seconds": config.connect_timeout_seconds,
            "ssl": True,
            "sslmode": "verify-full",
            "client_protocol_version": 0,
            "integrated_iam_for_explicit_credentials": False,
            "schemas_or_queries_allowed": False,
        },
    }
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(payload), encoding="utf-8")

    diagnostic._load_approval(approval, config=config, identity=identity)
    cast("dict[str, object]", payload["execution"])["sslmode"] = "disable"
    approval.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="protected execution"):
        diagnostic._load_approval(approval, config=config, identity=identity)


def test_diagnostic_requires_zero_provider_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    with pytest.raises(ValueError, match="exactly one provider attempt"):
        diagnostic.run_diagnostic(_config(), serverless_client=_ServerlessClient())
