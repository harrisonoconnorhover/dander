"""Thin CLI coverage for manifest-bound Azure Container Apps operations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import dander.cli.azure_command as azure_command
from dander.cli.main import app
from dander.providers.azure_container_apps import (
    AzureContainerAppsExecution,
    AzureContainerAppsLogEvent,
    AzureDeploymentVerification,
    AzureSecretMetadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch

_JOB = "dander-00626d3b5f01"
_EXECUTION = f"{_JOB}-abc1234"


class _Operations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.execution = AzureContainerAppsExecution(
            name=_EXECUTION,
            state="succeeded",
        )

    def start(self) -> AzureContainerAppsExecution:
        self.calls.append(("start", None))
        return self.execution

    def start_identity_refresh_probe(self, **kwargs: object) -> AzureContainerAppsExecution:
        self.calls.append(("identity-refresh-probe", kwargs))
        return self.execution

    def latest(self) -> AzureContainerAppsExecution:
        self.calls.append(("latest", None))
        return self.execution

    def describe(self, execution_name: str) -> AzureContainerAppsExecution:
        self.calls.append(("describe", execution_name))
        return self.execution

    def logs(
        self,
        execution_name: str,
        *,
        limit: int,
    ) -> tuple[AzureContainerAppsLogEvent, ...]:
        self.calls.append(("logs", (execution_name, limit)))
        return (
            AzureContainerAppsLogEvent(
                timestamp="2026-08-11T12:00:01Z",
                message="runtime complete",
            ),
        )

    def cancel(self, execution_name: str) -> AzureContainerAppsExecution:
        self.calls.append(("cancel", execution_name))
        return self.execution

    def replay(self, execution_name: str) -> AzureContainerAppsExecution:
        self.calls.append(("replay", execution_name))
        return self.execution


def _install_fake(monkeypatch: MonkeyPatch) -> tuple[_Operations, dict[str, object]]:
    operations = _Operations()
    binding: dict[str, object] = {}

    def factory(**kwargs: object) -> _Operations:
        binding.update(kwargs)
        return operations

    monkeypatch.setattr(azure_command, "_azure_operations", factory)
    return operations, binding


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--deployment",
        "azure_snowflake",
        "--pipeline",
        "warehouse_fixture",
        "--config",
        str(tmp_path / "dander.yaml"),
    ]


def test_status_and_logs_bind_to_the_selected_manifest_pipeline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations, binding = _install_fake(monkeypatch)

    status = CliRunner().invoke(
        app,
        ["azure", "status", *_base_args(tmp_path), "--execution-name", _EXECUTION],
    )
    logs = CliRunner().invoke(
        app,
        [
            "azure",
            "logs",
            *_base_args(tmp_path),
            "--execution-name",
            _EXECUTION,
            "--limit",
            "7",
        ],
    )

    assert status.exit_code == 0, status.output
    assert logs.exit_code == 0, logs.output
    assert '"state": "succeeded"' in status.output
    assert '"message": "runtime complete"' in logs.output
    assert operations.calls == [("describe", _EXECUTION), ("logs", (_EXECUTION, 7))]
    assert binding == {
        "config": tmp_path / "dander.yaml",
        "deployment": "azure_snowflake",
        "pipeline": "warehouse_fixture",
        "name": "dander",
    }


@pytest.mark.parametrize(
    ("command", "extra", "expected"),
    [
        ("run", [], ("start", None)),
        ("cancel", ["--execution-name", _EXECUTION], ("cancel", _EXECUTION)),
        ("replay", ["--execution-name", _EXECUTION], ("replay", _EXECUTION)),
    ],
)
def test_mutating_operations_require_confirmation_and_invoke_one_action(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    command: str,
    extra: list[str],
    expected: tuple[str, object],
) -> None:
    operations, _binding = _install_fake(monkeypatch)

    refused = CliRunner().invoke(
        app,
        ["azure", command, *_base_args(tmp_path), *extra],
        input="n\n",
    )
    accepted = CliRunner().invoke(
        app,
        ["azure", command, *_base_args(tmp_path), *extra],
        input="y\n",
    )

    assert refused.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert operations.calls == [expected]


def test_identity_refresh_probe_requires_named_profile_and_confirmation(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations, _binding = _install_fake(monkeypatch)
    monkeypatch.setattr(
        azure_command,
        "load_project_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            launcher_provider="azure_container_apps",
            warehouse_provider="bigquery",
            state_provider="bigquery",
            catalog_provider="dataplex",
            secret_provider="gcp_secret_manager",
        ),
    )
    args = [
        "azure",
        "identity-refresh-probe",
        "--deployment",
        "azure_bigquery",
        "--pipeline",
        "warehouse_fixture",
        "--config",
        str(tmp_path / "dander.yaml"),
        "--project",
        "unit-project",
        "--dataset",
        "raw",
        "--table",
        "proof_rows",
    ]

    refused = CliRunner().invoke(app, args, input="n\n")
    accepted = CliRunner().invoke(app, args, input="y\n")

    assert refused.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert operations.calls == [
        (
            "identity-refresh-probe",
            {
                "project": "unit-project",
                "dataset": "raw",
                "table": "proof_rows",
                "max_wait_seconds": 900,
                "refresh_margin_seconds": 15,
            },
        )
    ]


def test_canonical_preflight_requires_exact_profile_and_emits_sanitized_metadata(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    binding = object()

    class _Binding:
        @classmethod
        def from_project(cls, **kwargs: object) -> object:
            calls.append(("binding", kwargs))
            return binding

    class _Verifier:
        def __init__(self, selected: object) -> None:
            assert selected is binding

        def verify(self, *, expected_image: str) -> AzureDeploymentVerification:
            calls.append(("verify", expected_image))
            return AzureDeploymentVerification(
                subscription="11111111-1111-4111-8111-111111111111",
                resource_group="dander-phase6",
                environment="environment-id",
                job="job-id",
                trigger_type="manual",
                image=expected_image,
                registry="registry-id",
                managed_identity="identity-id",
                key_vault="vault-id",
                log_analytics_workspace="workspace-id",
            )

        def verify_declared_secret_metadata(self) -> tuple[AzureSecretMetadata, ...]:
            calls.append(("secret-metadata", None))
            return (AzureSecretMetadata(name="postgres-dsn", enabled=True),)

    monkeypatch.setattr(
        azure_command,
        "load_project_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            launcher_provider="azure_container_apps",
            warehouse_provider="snowflake",
            state_provider="postgresql",
            catalog_provider="none",
            secret_provider="azure_key_vault",
            warehouse_config={
                "auth": {
                    "method": "oauth",
                    "token_env": "DANDER_SNOWFLAKE_OAUTH_TOKEN",
                }
            },
            state_config={"dsn_env": "DANDER_POSTGRES_DSN"},
            pipelines={
                "warehouse_fixture": SimpleNamespace(
                    secrets={
                        "DANDER_POSTGRES_DSN": "postgres-dsn",
                        "DANDER_SNOWFLAKE_OAUTH_TOKEN": "snowflake-oauth-token",
                    }
                )
            },
        ),
    )
    monkeypatch.setattr(azure_command, "AzureDeploymentBinding", _Binding)
    monkeypatch.setattr(azure_command, "AzureDeploymentVerifier", _Verifier)
    image = "danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64

    result = CliRunner().invoke(
        app,
        [
            "azure",
            "canonical-preflight",
            *_base_args(tmp_path),
            "--expected-image",
            image,
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"name": "postgres-dsn"' in result.output
    assert '"enabled": true' in result.output
    assert "value" not in result.output
    assert calls == [
        (
            "binding",
            {
                "config": tmp_path / "dander.yaml",
                "deployment": "azure_snowflake",
                "pipeline_id": "warehouse_fixture",
                "name": "dander",
            },
        ),
        ("verify", image),
        ("secret-metadata", None),
    ]


def test_canonical_preflight_rejects_other_compositions_before_provider_access(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        azure_command,
        "load_project_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            launcher_provider="azure_container_apps",
            warehouse_provider="bigquery",
            state_provider="bigquery",
            catalog_provider="dataplex",
            secret_provider="gcp_secret_manager",
        ),
    )

    class _ForbiddenBinding:
        @classmethod
        def from_project(cls, **_kwargs: object) -> object:
            pytest.fail("Provider binding must not be resolved")

    monkeypatch.setattr(azure_command, "AzureDeploymentBinding", _ForbiddenBinding)

    result = CliRunner().invoke(
        app,
        [
            "azure",
            "canonical-preflight",
            *_base_args(tmp_path),
            "--expected-image",
            "danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64,
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "requires the named Azure/Snowflake/PostgreSQL" in str(result.exception)


def test_canonical_preflight_rejects_non_oauth_snowflake_before_provider_access(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        azure_command,
        "load_project_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            launcher_provider="azure_container_apps",
            warehouse_provider="snowflake",
            state_provider="postgresql",
            catalog_provider="none",
            secret_provider="azure_key_vault",
            warehouse_config={
                "auth": {
                    "method": "key_pair",
                    "private_key_file_env": "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE",
                }
            },
            state_config={"dsn_env": "DANDER_POSTGRES_DSN"},
            pipelines={},
        ),
    )

    class _ForbiddenBinding:
        @classmethod
        def from_project(cls, **_kwargs: object) -> object:
            pytest.fail("Provider binding must not be resolved")

    monkeypatch.setattr(azure_command, "AzureDeploymentBinding", _ForbiddenBinding)
    result = CliRunner().invoke(
        app,
        [
            "azure",
            "canonical-preflight",
            *_base_args(tmp_path),
            "--expected-image",
            "danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64,
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "requires Snowflake OAuth" in str(result.exception)


def test_image_promotion_requires_confirmation_and_passes_only_typed_inputs(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    digest = "sha256:" + "a" * 64

    class _Promoter:
        artifact_record_path = tmp_path / ".dander" / "runtime-artifact-azure.json"

        def __init__(self, project_dir: Path) -> None:
            assert project_dir == tmp_path

        def promote(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return f"danderphase6.azurecr.io/dander/runtime@{digest}"

    monkeypatch.setattr(azure_command, "AzureRuntimeImagePromoter", _Promoter)
    args = [
        "image-promote-azure",
        "--source-image",
        f"registry.example/source@{digest}",
        "--subscription-id",
        "11111111-1111-4111-8111-111111111111",
        "--acr-name",
        "danderphase6",
        "--config",
        str(tmp_path / "dander.yaml"),
    ]

    refused = CliRunner().invoke(app, args, input="n\n")
    accepted = CliRunner().invoke(app, args, input="y\n")

    assert refused.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert calls == [
        {
            "source_image": f"registry.example/source@{digest}",
            "subscription_id": "11111111-1111-4111-8111-111111111111",
            "acr_name": "danderphase6",
            "repository_name": "dander/runtime",
            "tag_prefix": "promoted",
        }
    ]
