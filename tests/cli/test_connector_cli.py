"""Read-only connector capability CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

import dander.cli.main as cli_module
from dander.cli.main import app
from dander.ingestion import (
    ConnectionStatus,
    DeleteOutcome,
    Source,
    SourceCapabilities,
    SourceConfig,
)
from dander.security import OAuthTokenError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import pytest

_REPO_ROOT = Path(__file__).parents[2]


class _CheckableSource(Source):
    def __init__(self, config: SourceConfig, status: ConnectionStatus) -> None:
        super().__init__(config)
        self._status = status
        self.checked = 0

    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(
        self,
        endpoint: str,
        *,
        since: str | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        del endpoint, since
        return iter(())

    def test_connection(self) -> ConnectionStatus:
        self.checked += 1
        return self._status


class _DeletedFeedSource(_CheckableSource):
    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config, ConnectionStatus(ok=True))
        self.calls: list[tuple[str, str | None]] = []

    def get_deleted(
        self,
        endpoint: str,
        *,
        since: str | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        self.calls.append((endpoint, since))
        yield {"Id": "001000000000001AAA"}
        yield {"Id": "001000000000002AAA"}


class _WritableSource(_CheckableSource):
    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config, ConnectionStatus(ok=True))
        self.records: dict[str, dict[str, Any]] = {}

    def create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        assert endpoint == "accounts"
        created = {"Id": "001000000000001AAA", **record}
        self.records[created["Id"]] = created
        return created

    def update(
        self,
        endpoint: str,
        identity: Mapping[str, str],
        changes: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert endpoint == "accounts"
        current = self.records[identity["Id"]]
        current.update(changes)
        return current

    def upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        assert endpoint == "accounts"
        current = self.records.setdefault(str(record["Id"]), {})
        current.update(record)
        return current

    def delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome:
        assert endpoint == "accounts"
        if self.records.pop(identity["Id"], None) is None:
            return DeleteOutcome.NOT_FOUND
        return DeleteOutcome.DELETED


def _config() -> SourceConfig:
    return SourceConfig(
        name="example",
        base_url="https://example.test",
        engine="dlt",
        auth_strategy="none",
    )


def test_inspect_resolves_pipeline_and_lists_capabilities_without_provider_call(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "dander.yaml"
    manifest.write_text(
        """
version: 1
pipelines:
  greenhouse_control:
    source: greenhouse_job_board
    models: []
    build_models: false
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "connector",
            "inspect",
            "greenhouse_control",
            "--config",
            str(manifest),
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dander connector:" in result.output
    assert "greenhouse_job_board" in result.output
    assert "get_single_object" in result.output
    assert "test_connection" in result.output
    assert "dlt" in result.output
    assert "yes" not in result.output


def test_check_invokes_supported_probe_and_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _CheckableSource(_config(), ConnectionStatus(ok=True))
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )

    result = CliRunner().invoke(
        app,
        ["connector", "check", "example", "--config", str(tmp_path / "missing.yaml")],
    )

    assert result.exit_code == 0, result.output
    assert "connection check passed" in result.output
    assert source.checked == 1


def test_check_reports_provider_refusal_without_exposing_an_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _CheckableSource(_config(), ConnectionStatus(ok=False, detail="permission denied"))
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )

    result = CliRunner().invoke(
        app,
        ["connector", "check", "example", "--config", str(tmp_path / "missing.yaml")],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "connection check failed: permission denied" in str(result.exception)


def test_check_fails_clearly_when_source_does_not_support_probe(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "connector",
            "check",
            "greenhouse_job_board",
            "--config",
            str(tmp_path / "missing.yaml"),
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "does not support operation 'test_connection'" in str(result.exception)


def test_check_reports_core_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _CheckableSource(_config(), ConnectionStatus(ok=True))

    def fail_authentication() -> ConnectionStatus:
        raise OAuthTokenError("OAuth token request failed")

    source.test_connection = fail_authentication  # type: ignore[method-assign]
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )

    result = CliRunner().invoke(
        app,
        ["connector", "check", "example", "--config", str(tmp_path / "missing.yaml")],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "OAuth token request failed" in str(result.exception)


def test_get_deleted_streams_json_lines_and_forwards_cursor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _DeletedFeedSource(_config())
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )

    result = CliRunner().invoke(
        app,
        [
            "connector",
            "get-deleted",
            "example",
            "accounts",
            "--since",
            "2026-08-01T00:00:00Z",
            "--config",
            str(tmp_path / "missing.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        '{"Id":"001000000000001AAA"}',
        '{"Id":"001000000000002AAA"}',
    ]
    assert source.calls == [("accounts", "2026-08-01T00:00:00Z")]


def test_get_deleted_fails_clearly_when_capability_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _CheckableSource(_config(), ConnectionStatus(ok=True))
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )

    result = CliRunner().invoke(
        app,
        [
            "connector",
            "get-deleted",
            "example",
            "accounts",
            "--config",
            str(tmp_path / "missing.yaml"),
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "does not support operation 'get_deleted'" in str(result.exception)


def test_write_dispatches_each_operation_against_stateful_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _WritableSource(_config())
    monkeypatch.setattr(
        cli_module,
        "_load_connector_capabilities",
        lambda *_args, **_kwargs: (source.config, SourceCapabilities(source)),
    )
    inputs = {
        "record": {"Name": "Acme"},
        "identity": {"Id": "001000000000001AAA"},
        "changes": {"Name": "Acme Updated"},
        "upsert": {"Id": "001000000000001AAA", "Name": "Acme Final"},
    }
    paths: dict[str, Path] = {}
    for name, payload in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    commands = [
        ("create", ["--record", str(paths["record"])], "Acme"),
        (
            "update",
            ["--identity", str(paths["identity"]), "--changes", str(paths["changes"])],
            "Acme Updated",
        ),
        ("upsert", ["--record", str(paths["upsert"])], "Acme Final"),
        ("delete", ["--identity", str(paths["identity"])], "deleted"),
    ]
    for operation, options, expected in commands:
        result = CliRunner().invoke(
            app,
            [
                "connector",
                "write",
                "example",
                "accounts",
                operation,
                *options,
                "--confirm-write",
                "--config",
                str(tmp_path / "missing.yaml"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert expected in result.output

    assert source.records == {}


def test_write_requires_explicit_confirmation_before_loading_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    loaded = False

    def load_source(*_args: object, **_kwargs: object) -> None:
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(cli_module, "_load_connector_capabilities", load_source)
    result = CliRunner().invoke(
        app,
        ["connector", "write", "example", "accounts", "delete"],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "--confirm-write" in str(result.exception)
    assert loaded is False
