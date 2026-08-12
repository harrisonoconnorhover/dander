"""OCI plan-first CLI command boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from dander.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
