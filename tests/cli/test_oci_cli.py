"""OCI plan-first CLI command boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner

import dander.cli.oci_command as oci_command
from dander.cli.main import app
from dander.project import ProjectConfigError

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


def test_oci_admin_plan_is_non_mutating_and_prints_review_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_execute(self: object, **kwargs: object) -> Path:
        del self
        captured.update(kwargs)
        return tmp_path / "operator/dander-oci-admin-bootstrap.tfplan"

    monkeypatch.setattr(
        "dander.cli.oci_command.OciAdministrativeBootstrap.execute",
        fake_execute,
    )
    operator = tmp_path / "operator"
    result = CliRunner().invoke(
        app,
        [
            "init-oci-admin-plan",
            "--tenancy-id",
            "ocid1.tenancy.oc1.." + "a" * 32,
            "--compartment-id",
            "ocid1.compartment.oc1.." + "b" * 32,
            "--state-bucket",
            "dander-phase7-state",
            "--oci-profile",
            "DANDER",
            "--operator-artifact-dir",
            str(operator),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["repository_name"] == "dander/runtime"
    assert captured["config_file_profile"] == "DANDER"
    assert "OCI administrative bootstrap planned" in result.output
    assert "terraform-workspace" in result.output
    assert "No OCI resources were created" in result.output


def test_oci_image_promotion_requires_confirmation_and_passes_typed_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    digest = "sha256:" + "a" * 64

    class _Promoter:
        artifact_record_path = tmp_path / ".dander" / "runtime-artifact-oci.json"

        def __init__(self, project_dir: Path) -> None:
            assert project_dir == tmp_path

        def promote(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return f"ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime@{digest}"

    monkeypatch.setattr(oci_command, "OciRuntimeImagePromoter", _Promoter)
    args = [
        "image-promote-oci",
        "--source-image",
        f"registry.example/source@{digest}",
        "--compartment-id",
        "ocid1.compartment.oc1.." + "b" * 32,
        "--registry-namespace",
        "unitnamespace",
        "--oci-profile",
        "DANDER",
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
            "compartment_id": "ocid1.compartment.oc1.." + "b" * 32,
            "region": "us-ashburn-1",
            "namespace": "unitnamespace",
            "repository_name": "dander/runtime",
            "oci_profile": "DANDER",
            "tag_prefix": "promoted",
        }
    ]


def test_oci_foundation_apply_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def fake_apply(self: object, **kwargs: object) -> Path:
        del self, kwargs
        nonlocal called
        called = True
        return tmp_path / "operator/dander-oci-foundation.tfplan"

    monkeypatch.setattr(
        "dander.cli.oci_command.OciTerraformBootstrap.apply_saved_plan",
        fake_apply,
    )
    result = CliRunner().invoke(
        app,
        [
            "init-oci-apply",
            "--tenancy-id",
            "ocid1.tenancy.oc1.." + "a" * 32,
            "--compartment-id",
            "ocid1.compartment.oc1.." + "b" * 32,
            "--object-storage-namespace",
            "unitnamespace",
            "--state-bucket",
            "dander-phase7-state",
            "--operator-artifact-dir",
            str(tmp_path / "operator"),
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert called is False


def test_oci_deployment_verifier_checks_stage_zero_and_foundation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verified: list[dict[str, object]] = []

    def fake_verify(self: object, **kwargs: object) -> None:
        del self
        verified.append(kwargs)

    monkeypatch.setattr(
        "dander.cli.oci_command.OciAdministrativeBootstrap.verify_no_drift",
        fake_verify,
    )
    monkeypatch.setattr(
        "dander.cli.oci_command.OciTerraformBootstrap.verify_no_drift",
        fake_verify,
    )
    result = CliRunner().invoke(
        app,
        [
            "verify-oci-deployment",
            "--tenancy-id",
            "ocid1.tenancy.oc1.." + "a" * 32,
            "--compartment-id",
            "ocid1.compartment.oc1.." + "b" * 32,
            "--object-storage-namespace",
            "unitnamespace",
            "--state-bucket",
            "dander-phase7-state",
            "--admin-operator-artifact-dir",
            str(tmp_path / "admin"),
            "--foundation-operator-artifact-dir",
            str(tmp_path / "foundation"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(verified) == 2
    assert verified[0]["state_key"] == "dander/oci/bootstrap-admin/terraform.tfstate"
    assert verified[1]["state_key"] == "dander/oci/foundation/terraform.tfstate"
    assert '"status": "no_drift"' in result.output


def test_launcher_projection_requires_controller_tag_in_selected_immutable_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = {
        "tenancy_id": "ocid1.tenancy.oc1.." + "a" * 32,
        "compartment_id": "ocid1.compartment.oc1.." + "b" * 32,
        "region": "us-ashburn-1",
        "registry_namespace": "unitnamespace",
        "repository_name": "dander/runtime",
    }
    manifest = SimpleNamespace(
        launcher_provider="oci_container_instances",
        validate_references=lambda _path: None,
        resolved_launcher_config=lambda: launcher,
        platform=SimpleNamespace(
            runtime=SimpleNamespace(
                cpu=1,
                memory="2Gi",
                timeout_seconds=900,
                max_retries=1,
                batch_rows=1_000,
            ),
            safety=SimpleNamespace(require_guarded_free_tier=True),
        ),
        terraform_pipelines=lambda: {},
    )
    monkeypatch.setattr(oci_command, "load_project_config", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(oci_command, "build_oci_execution_projections", lambda **_kwargs: {})
    common = {
        "config": tmp_path / "dander.yaml",
        "platforms_config": None,
        "deployment": "oci_postgresql",
        "container_image": (
            "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime@sha256:" + "a" * 64
        ),
        "tenancy_id": launcher["tenancy_id"],
        "compartment_id": launcher["compartment_id"],
        "region": launcher["region"],
        "namespace": launcher["registry_namespace"],
    }

    assert (
        oci_command._deployment_projections(  # noqa: SLF001
            **cast("Any", common),
            controller_image=(
                "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/"
                "dander/runtime:phase7-controller"
            ),
        )
        == {}
    )
    with pytest.raises(ProjectConfigError, match="manifest-selected OCIR repository"):
        oci_command._deployment_projections(  # noqa: SLF001
            **cast("Any", common),
            controller_image=(
                "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/"
                "dander/controller:phase7-controller"
            ),
        )
