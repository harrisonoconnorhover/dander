"""Focused tests for the live Phase 1B artifact and identity proof tooling."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from scripts.portability.oci_copy import CopyVerificationError, copy_and_verify
from scripts.portability.prepare_phase1b_context import ContextPreparationError, prepare_context
from scripts.portability.scan_long_lived_credentials import scan
from scripts.portability.wif_bigquery_probe import ProbeError, run_probe

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_INDEX_DIGEST = "sha256:" + "a" * 64
_AMD64_DIGEST = "sha256:" + "b" * 64
_ARM64_DIGEST = "sha256:" + "c" * 64


def _manifest() -> bytes:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": _AMD64_DIGEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": _ARM64_DIGEST,
                    "platform": {
                        "os": "linux",
                        "architecture": "arm64",
                        "variant": "v8",
                    },
                },
                {
                    "digest": "sha256:" + "d" * 64,
                    "platform": {"os": "unknown", "architecture": "unknown"},
                },
            ],
        },
        separators=(",", ":"),
    ).encode()


class _RegistryRunner:
    def __init__(
        self,
        *,
        destination_digest: str = _INDEX_DIGEST,
        destination_manifest: bytes | None = None,
    ) -> None:
        self.destination_digest = destination_digest
        self.destination_manifest = destination_manifest
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> bytes:
        args = tuple(command)
        self.commands.append(args)
        if args[:2] == ("crane", "copy"):
            return b""
        if args[:2] == ("crane", "manifest"):
            if "ecr" in args[2] and self.destination_manifest is not None:
                return self.destination_manifest
            return _manifest()
        if args[:2] == ("crane", "digest"):
            return (self.destination_digest if "ecr" in args[2] else _INDEX_DIGEST).encode()
        raise AssertionError(args)


def test_oci_copy_preserves_index_and_platform_digests() -> None:
    source = f"us-central1-docker.pkg.dev/unit/dander/dander@{_INDEX_DIGEST}"
    destination = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander:phase1b"
    runner = _RegistryRunner()

    record = copy_and_verify(source=source, destination=destination, runner=runner)

    assert record["index_digest"] == _INDEX_DIGEST
    assert record["destination"] == destination.rsplit(":", 1)[0] + f"@{_INDEX_DIGEST}"
    assert record["platform_manifests"] == {
        "linux/amd64": _AMD64_DIGEST,
        "linux/arm64/v8": _ARM64_DIGEST,
    }
    assert record["copied_without_rebuild"] is True
    assert runner.commands[2] == ("crane", "copy", source, destination)
    assert all("build" not in command for command in runner.commands)


def test_oci_copy_fails_when_destination_rewrites_the_index() -> None:
    with pytest.raises(CopyVerificationError, match="rewrote"):
        copy_and_verify(
            source=f"gar.example.invalid/dander@{_INDEX_DIGEST}",
            destination="ecr.example.invalid/dander:phase1b",
            runner=_RegistryRunner(destination_digest="sha256:" + "e" * 64),
        )


def test_oci_copy_fails_when_destination_changes_the_index_document() -> None:
    changed = _manifest().replace(b'"schemaVersion":2', b'"schemaVersion": 2')
    with pytest.raises(CopyVerificationError, match="changed the OCI index"):
        copy_and_verify(
            source=f"gar.example.invalid/dander@{_INDEX_DIGEST}",
            destination="ecr.example.invalid/dander:phase1b",
            runner=_RegistryRunner(destination_manifest=changed),
        )


class _Credentials:
    def __init__(self, expiry: datetime) -> None:
        self.expiry: datetime | None = expiry


class _QueryClient:
    def __init__(self, credentials: _Credentials) -> None:
        self.credentials = credentials
        self.calls = 0

    def query_count(self, *, project: str, dataset: str, table: str) -> int:
        assert (project, dataset, table) == ("unit-project", "raw", "records")
        self.calls += 1
        if self.calls == 2:
            assert self.credentials.expiry is not None
            self.credentials.expiry += timedelta(minutes=10)
        return 7


def test_wif_probe_observes_expiry_refresh_without_logging_tokens() -> None:
    started = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    credentials = _Credentials(started + timedelta(minutes=10))
    client = _QueryClient(credentials)
    records: list[dict[str, object]] = []
    waits: list[float] = []

    run_probe(
        credentials=credentials,
        client=client,
        project="unit-project",
        dataset="raw",
        table="records",
        max_wait_seconds=700,
        refresh_margin_seconds=5,
        now=lambda: started,
        sleep=waits.append,
        emit=records.append,
    )

    assert waits == [605.0]
    assert [record["event"] for record in records] == [
        "query.completed",
        "query.completed",
        "credential.refresh_observed",
    ]
    assert "token" not in json.dumps(records).lower()
    assert client.calls == 2


def test_wif_probe_rejects_an_unbounded_credential_lifetime() -> None:
    started = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    credentials = _Credentials(started + timedelta(hours=1))
    with pytest.raises(ProbeError, match="bounded proof window"):
        run_probe(
            credentials=credentials,
            client=_QueryClient(credentials),
            project="unit-project",
            dataset="raw",
            table="records",
            max_wait_seconds=900,
            refresh_margin_seconds=5,
            now=lambda: started,
            sleep=lambda _seconds: None,
            emit=lambda _record: None,
        )


def _write_generated_project(path: Path) -> None:
    path.mkdir()
    (path / "Dockerfile").write_text(
        "FROM python:3.12-slim\n\nUSER 65532:65532\n",
        encoding="utf-8",
    )
    (path / ".dockerignore").write_text("*\n!Dockerfile\n", encoding="utf-8")
    (path / "dander.yaml").write_text("version: 1\n", encoding="utf-8")


def _write_aws_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "external_account",
                "audience": (
                    "//iam.googleapis.com/projects/123/locations/global/pools/p/providers/a"
                ),
                "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
                "token_url": "https://sts.googleapis.com/v1/token",
                "service_account_impersonation_url": (
                    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                    "probe@unit.iam.gserviceaccount.com:generateAccessToken"
                ),
                "credential_source": {"environment_id": "aws1"},
            }
        ),
        encoding="utf-8",
    )


def test_prepare_context_keeps_the_project_source_free_and_bounds_token_lifetime(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write_generated_project(project)
    config = tmp_path / "external.json"
    _write_aws_config(config)
    probe = tmp_path / "probe.py"
    probe.write_text("print('probe')\n", encoding="utf-8")

    prepare_context(
        project_dir=project,
        credential_config=config,
        probe_script=probe,
    )
    prepare_context(
        project_dir=project,
        credential_config=config,
        probe_script=probe,
    )

    assert not (project / "src").exists()
    rendered = json.loads((project / "gcp-wif.json").read_text(encoding="utf-8"))
    assert rendered["type"] == "external_account"
    assert rendered["service_account_impersonation_options"] == {"token_lifetime_seconds": 600}
    assert (project / "Dockerfile").read_text(encoding="utf-8").count(
        "COPY --chown=65532:65532 phase1b_probe.py"
    ) == 1
    assert (project / ".dockerignore").read_text(encoding="utf-8").count("!gcp-wif.json") == 1


def test_prepare_context_rejects_a_service_account_key(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_generated_project(project)
    config = tmp_path / "service-account.json"
    config.write_text('{"type":"service_account","private_key":"nope"}', encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text("pass\n", encoding="utf-8")

    with pytest.raises(ContextPreparationError, match="external-account"):
        prepare_context(
            project_dir=project,
            credential_config=config,
            probe_script=probe,
        )


def test_long_lived_credential_scan_reports_only_path_and_pattern(tmp_path: Path) -> None:
    safe = tmp_path / "external.json"
    safe.write_text('{"type":"external_account"}', encoding="utf-8")
    unsafe = tmp_path / "bad.json"
    fake_key = (
        "-----BEGIN PRIVATE KEY-----\n" + "A" * 64 + "\n" + "B" * 64 + "\n-----END PRIVATE KEY-----"
    )
    unsafe.write_text(
        json.dumps({"type": "service_account", "private_key": fake_key}),
        encoding="utf-8",
    )

    findings = scan([tmp_path])

    assert findings == [
        {"path": str(unsafe), "pattern": "gcp_private_key"},
    ]
    assert "redacted" not in json.dumps(findings)
