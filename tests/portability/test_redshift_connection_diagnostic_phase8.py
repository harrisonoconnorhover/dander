"""Credential-free checks for the Redshift connection-boundary diagnostic."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import redshift_connection_diagnostic_phase8 as diagnostic

import dander.providers.redshift.runtime as redshift_runtime_module
from dander import __version__

if TYPE_CHECKING:
    from collections.abc import Mapping


_COMMIT = "a" * 40
_DIGEST = f"sha256:{'b' * 64}"
_REFERENCE = "codex-goal-redshift-connection-diagnostic"


class _Connection:
    def __init__(self, *, path: str) -> None:
        self.path = path
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ServerlessClient:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        events: list[str] | None = None,
        event_name: str = "get_credentials",
        username: str = "temporary-user",
        password: str = "temporary-password",
    ) -> None:
        self.failure = failure
        self.events = events
        self.event_name = event_name
        self.username = username
        self.password = password
        self.calls: list[dict[str, object]] = []

    def get_credentials(self, **kwargs: object) -> Mapping[str, object]:
        if self.events is not None:
            self.events.append(self.event_name)
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return {"dbUser": self.username, "dbPassword": self.password}


def _config() -> diagnostic.DiagnosticConfig:
    return diagnostic.DiagnosticConfig(
        account_id="123456789012",
        host="workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-connect-diag",
        copy_role_arn="arn:aws:iam::123456789012:role/dander-redshift-copy",
        staging_bucket="dander-redshift-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
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
    fail_current: Exception | None = None,
    events: list[str] | None = None,
) -> _ServerlessClient:
    current_client = _ServerlessClient(
        events=events,
        event_name="dander_get_credentials",
        username="dander-temporary-user",
        password="dander-temporary-password",
    )

    def explicit_connect(**kwargs: object) -> object:
        calls.append(kwargs)
        is_current = kwargs.get("user") == "dander-temporary-user"
        if events is not None:
            events.append("dander_connect" if is_current else "explicit_connect")
        failure = fail_current if is_current else fail_explicit
        if failure is not None:
            raise failure
        return _Connection(path="dander" if is_current else "explicit")

    module = ModuleType("redshift_connector")
    module.connect = explicit_connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redshift_connector", module)
    monkeypatch.setattr(
        redshift_runtime_module,
        "_sdk_serverless_client",
        lambda _region: current_client,
    )
    return current_client


def test_diagnostic_compares_explicit_credentials_with_current_rc32_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    events: list[str] = []
    client = _ServerlessClient(events=events)
    calls: list[dict[str, object]] = []
    current_client = _install_connector(monkeypatch, calls, events=events)

    def execute(connection: object, statement: str, *, fetch: str) -> object:
        current = cast("_Connection", connection)
        events.append(f"{current.path}_query")
        assert statement == "SELECT current_database(), current_user"
        assert fetch == "one"
        return SimpleNamespace(row=("analytics", "IAMR:dander_runtime"))

    monkeypatch.setattr(diagnostic, "execute", execute)

    result = diagnostic.run_diagnostic(_config(), serverless_client=client)

    assert [stage["stage"] for stage in cast("list[dict[str, object]]", result["stages"])] == [
        "get_credentials",
        "explicit_credentials_connector",
        "explicit_credentials_validation_query",
        "dander_current_connector",
        "dander_current_validation_query",
    ]
    assert events == [
        "get_credentials",
        "explicit_connect",
        "explicit_query",
        "dander_get_credentials",
        "dander_connect",
        "dander_query",
    ]
    assert all(
        stage["exception_class"] is None
        for stage in cast("list[dict[str, object]]", result["stages"])
    )
    assert client.calls == [
        {
            "workgroupName": "dander-p8q-rc32-rs-connect-diag",
            "dbName": "analytics",
            "durationSeconds": 900,
        }
    ]
    assert current_client.calls == client.calls
    assert len(calls) == 2
    explicit, current = calls
    assert explicit["user"] == "temporary-user"
    assert explicit["password"] == "temporary-password"
    assert current["user"] == "dander-temporary-user"
    assert current["password"] == "dander-temporary-password"
    for call in calls:
        assert call["iam"] is False
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
        fail_current=RuntimeError("private AWS response"),
    )

    result = diagnostic.run_diagnostic(_config(), serverless_client=_ServerlessClient())
    encoded = json.dumps(result)
    stages = cast("list[dict[str, object]]", result["stages"])

    assert [stage["exception_class"] for stage in stages] == [
        None,
        "TimeoutError",
        "DiagnosticPrerequisiteError",
        "RuntimeError",
        "DiagnosticPrerequisiteError",
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
    monkeypatch.setattr(
        diagnostic,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(row=("analytics", "IAMR:dander_runtime")),
    )

    result = diagnostic.run_diagnostic(
        _config(),
        serverless_client=_ServerlessClient(failure=PermissionError("private policy")),
    )
    stages = cast("list[dict[str, object]]", result["stages"])

    assert [stage["exception_class"] for stage in stages] == [
        "PermissionError",
        "DiagnosticPrerequisiteError",
        "DiagnosticPrerequisiteError",
        None,
        None,
    ]
    assert len(calls) == 1
    assert calls[0]["iam"] is False


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
            "maximum_manual_executions": 20,
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
        },
    }
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(payload), encoding="utf-8")

    diagnostic._load_approval(approval, config=config, identity=identity, execution_number=20)
    with pytest.raises(ValueError, match="execution number"):
        diagnostic._load_approval(approval, config=config, identity=identity, execution_number=21)
    cast("dict[str, object]", payload["execution"])["sslmode"] = "disable"
    approval.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="protected execution"):
        diagnostic._load_approval(approval, config=config, identity=identity, execution_number=1)


def test_historical_rc31_objective_preserves_old_path_order() -> None:
    root = Path(__file__).parents[2]
    approval = (
        root / "docs/evidence/phase8/2026-08-23/"
        "aws-native-rc31-redshift-connection-reproduction-objective.json"
    )
    payload = json.loads(approval.read_text(encoding="utf-8"))

    assert payload["schema"] == "io.dander.phase8.redshift-connection-diagnostic-approval/v2"
    assert payload["stages"][:2] == ["dander_iam_connector", "dander_validation_query"]
    assert payload["execution"]["harness_sha256"] == (
        "123b83902fa9630c5d1c80dcfcf2c4da3c7aaf94c567eb6c5e72858ddca1f4f5"
    )
    assert payload["execution"]["maximum_manual_executions"] == 20


def test_tracked_rc32_objective_matches_exact_harness_and_twenty_run_bound() -> None:
    root = Path(__file__).parents[2]
    approval = (
        root / "docs/evidence/phase8/2026-08-24/"
        "aws-native-rc32-redshift-connection-diagnostic-objective.json"
    )
    payload = json.loads(approval.read_text(encoding="utf-8"))
    provider = cast("dict[str, object]", payload["provider"])
    candidate = cast("dict[str, object]", payload["candidate"])
    execution = cast("dict[str, object]", payload["execution"])
    config = diagnostic.DiagnosticConfig(
        account_id=cast("str", provider["account_id"]),
        host=cast("str", provider["host"]),
        database=cast("str", provider["database"]),
        region=cast("str", provider["region"]),
        workgroup_name=cast("str", provider["workgroup_name"]),
        copy_role_arn="arn:aws:iam::184463061564:role/dander-redshift-copy",
        staging_bucket="dander-redshift-staging",
        staging_prefix="phase8/0.9.0rc32/staging",
        port=cast("int", provider["port"]),
    )
    identity = diagnostic.CandidateIdentity(
        release_version=cast("str", candidate["release_version"]),
        git_commit=cast("str", candidate["git_commit"]),
        image_digest=cast("str", candidate["image_digest"]),
        approval_reference=cast("str", execution["approval_reference"]),
    )

    diagnostic._load_approval(approval, config=config, identity=identity, execution_number=1)
    diagnostic._load_approval(approval, config=config, identity=identity, execution_number=20)


def test_diagnostic_requires_zero_provider_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    with pytest.raises(ValueError, match="exactly one provider attempt"):
        diagnostic.run_diagnostic(_config(), serverless_client=_ServerlessClient())
