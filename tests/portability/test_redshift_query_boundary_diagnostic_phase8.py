"""Credential-free checks for the RC32 Redshift query-boundary diagnostic."""

from __future__ import annotations

import json
import socket
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import redshift_query_boundary_diagnostic_phase8 as diagnostic

from dander import __version__

if TYPE_CHECKING:
    from collections.abc import Mapping


_COMMIT = "a" * 40
_DIGEST = f"sha256:{'b' * 64}"
_REFERENCE = "codex-redshift-query-boundary"


class _ServerlessClient:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def get_credentials(self, **kwargs: object) -> Mapping[str, object]:
        if self.events is not None:
            self.events.append("get_credentials")
        self.calls.append(kwargs)
        return {"dbUser": "temporary-user", "dbPassword": "temporary-password"}


class _Connection:
    def __init__(self, path: str) -> None:
        self.path = path
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SensitiveConnectionError(RuntimeError):
    """A provider error whose message must not reach diagnostic output."""


def _config() -> diagnostic.DiagnosticConfig:
    return diagnostic.DiagnosticConfig(
        account_id="123456789012",
        host="query.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        database="analytics",
        region="us-east-1",
        workgroup_name="dander-p8q-rc32-rs-query-c13",
    )


def _identity() -> diagnostic.CandidateIdentity:
    return diagnostic.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
    )


def test_diagnostic_isolates_verified_tls_psycopg_and_product_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    events: list[str] = []
    client = _ServerlessClient(events)
    psycopg_calls: list[dict[str, object]] = []
    redshift_calls: list[dict[str, object]] = []
    connections: list[_Connection] = []

    def connect_psycopg(**kwargs: object) -> object:
        events.append("psycopg_connector")
        psycopg_calls.append(kwargs)
        connection = _Connection("psycopg")
        connections.append(connection)
        return connection

    def connect_redshift(**kwargs: object) -> object:
        events.append("redshift_connector")
        redshift_calls.append(kwargs)
        connection = _Connection("redshift")
        connections.append(connection)
        return connection

    def execute(connection: object, statement: str, *, fetch: str) -> object:
        selected = cast("_Connection", connection)
        events.append(f"{selected.path}_query")
        assert statement == "SELECT 1"
        assert fetch == "one"
        return SimpleNamespace(row=(1,))

    monkeypatch.setattr(diagnostic, "execute", execute)
    result = diagnostic.run_diagnostic(
        _config(),
        serverless_client=client,
        tls_probe=lambda _config: events.append("verified_tls"),
        psycopg_connector=connect_psycopg,
        redshift_connector=connect_redshift,
    )

    stages = cast("list[dict[str, object]]", result["stages"])
    assert [stage["stage"] for stage in stages] == list(diagnostic._DIAGNOSTIC_STAGES)
    assert all(stage["exception_class"] is None for stage in stages)
    assert events == [
        "get_credentials",
        "verified_tls",
        "psycopg_connector",
        "psycopg_query",
        "redshift_connector",
        "redshift_query",
    ]
    assert client.calls == [
        {
            "workgroupName": "dander-p8q-rc32-rs-query-c13",
            "dbName": "analytics",
            "durationSeconds": 900,
        }
    ]
    assert psycopg_calls == [
        {
            "host": _config().host,
            "port": 5439,
            "dbname": "analytics",
            "user": "temporary-user",
            "password": "temporary-password",
            "sslmode": "verify-full",
            "sslrootcert": "system",
            "connect_timeout": 300,
            "application_name": "dander",
        }
    ]
    assert redshift_calls == [
        {
            "user": "temporary-user",
            "password": "temporary-password",
            "iam": False,
            "ssl": True,
            "sslmode": "verify-full",
            "host": _config().host,
            "port": 5439,
            "database": "analytics",
            "region": "us-east-1",
            "timeout": 300,
            "application_name": "dander",
            "client_protocol_version": 0,
            "is_serverless": True,
            "serverless_work_group": "dander-p8q-rc32-rs-query-c13",
        }
    ]
    assert all(connection.closed for connection in connections)


def test_diagnostic_continues_across_paths_and_sanitizes_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    redshift = _Connection("redshift")

    def execute(connection: object, _statement: str, *, fetch: str) -> object:
        assert fetch == "one"
        if cast("_Connection", connection).path == "redshift":
            raise SensitiveConnectionError("private endpoint response")
        return SimpleNamespace(row=(1,))

    monkeypatch.setattr(diagnostic, "execute", execute)
    result = diagnostic.run_diagnostic(
        _config(),
        serverless_client=_ServerlessClient(),
        tls_probe=lambda _config: (_ for _ in ()).throw(
            SensitiveConnectionError("private certificate detail")
        ),
        psycopg_connector=lambda **_kwargs: (_ for _ in ()).throw(
            SensitiveConnectionError("temporary-password")
        ),
        redshift_connector=lambda **_kwargs: redshift,
    )

    stages = cast("list[dict[str, object]]", result["stages"])
    assert [stage["exception_class"] for stage in stages] == [
        None,
        "SensitiveConnectionError",
        "SensitiveConnectionError",
        "DiagnosticPrerequisiteError",
        None,
        "SensitiveConnectionError",
    ]
    encoded = json.dumps(result)
    assert "temporary-password" not in encoded
    assert "private certificate" not in encoded
    assert "private endpoint" not in encoded
    assert all(set(stage) == {"stage", "elapsed_ms", "exception_class"} for stage in stages)
    assert redshift.closed


def test_tls_probe_uses_postgres_ssl_request_and_hostname_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class _Socket:
        def __enter__(self) -> _Socket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, timeout: int) -> None:
            calls.append(("timeout", timeout))

        def sendall(self, payload: bytes) -> None:
            calls.append(("request", payload))

        def recv(self, size: int) -> bytes:
            calls.append(("recv", size))
            return b"S"

    class _Secured:
        def __enter__(self) -> _Secured:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def do_handshake(self) -> None:
            calls.append("handshake")

    class _Context:
        def wrap_socket(self, raw: object, *, server_hostname: str) -> _Secured:
            calls.append(("wrap", raw, server_hostname))
            return _Secured()

    raw = _Socket()

    def create_connection(address: object, *, timeout: int) -> _Socket:
        calls.append(("connect", address, timeout))
        return raw

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: _Context())

    diagnostic._verified_postgres_tls_handshake(_config())

    assert calls == [
        ("connect", (_config().host, 5439), 300),
        ("timeout", 300),
        ("request", diagnostic._POSTGRES_SSL_REQUEST),
        ("recv", 1),
        ("wrap", raw, _config().host),
        "handshake",
    ]


def test_approval_binds_one_read_only_execution_and_exact_harness(tmp_path: Path) -> None:
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
    }
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(payload), encoding="utf-8")

    diagnostic._load_approval(approval, config=config, identity=identity, execution_number=1)
    with pytest.raises(ValueError, match="execution number"):
        diagnostic._load_approval(approval, config=config, identity=identity, execution_number=2)
    cast("dict[str, object]", payload["execution"])["sslmode"] = "disable"
    approval.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="protected execution"):
        diagnostic._load_approval(approval, config=config, identity=identity, execution_number=1)


def test_diagnostic_requires_zero_provider_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")

    with pytest.raises(ValueError, match="exactly one provider attempt"):
        diagnostic.run_diagnostic(_config(), serverless_client=_ServerlessClient())
