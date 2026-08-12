"""Saved-plan OCI administrative and foundation bootstrap behavior."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from dander.bootstrap import (
    OciAdministrativeBootstrap,
    OciTerraformBootstrap,
    OciTerraformBootstrapError,
)

if TYPE_CHECKING:
    from typing import Any

_ROOT = Path(__file__).parents[2]
_TENANCY = "ocid1.tenancy.oc1.." + "a" * 32
_COMPARTMENT = "ocid1.compartment.oc1.." + "b" * 32
_ADMIN = {
    "tenancy_id": _TENANCY,
    "compartment_id": _COMPARTMENT,
    "region": "us-ashburn-1",
    "state_bucket_name": "dander-phase7-state",
    "state_key": "dander/oci/bootstrap-admin/terraform.tfstate",
    "repository_name": "dander/runtime",
    "config_file_profile": "DANDER",
}
_FOUNDATION = {
    "tenancy_id": _TENANCY,
    "compartment_id": _COMPARTMENT,
    "region": "us-ashburn-1",
    "namespace": "unitnamespace",
    "state_bucket_name": "dander-phase7-state",
    "state_key": "dander/oci/foundation/terraform.tfstate",
    "dynamic_group_name": "dander_phase7_runtime",
    "config_file_profile": "DANDER",
    "name": "dander",
}


@pytest.fixture
def terraform_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        output = next(
            (value.removeprefix("-out=") for value in args if value.startswith("-out=")),
            None,
        )
        if output is not None:
            Path(output).write_bytes(b"reviewed-plan")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_admin_bootstrap_applies_exact_plan_then_migrates_native_state(
    tmp_path: Path,
    terraform_calls: list[tuple[str, ...]],
) -> None:
    operator_dir = tmp_path / "operator"
    bootstrap = OciAdministrativeBootstrap(
        _ROOT / "infra/oci/bootstrap-admin",
        operator_dir,
    )

    plan = bootstrap.execute(**_ADMIN)
    result = bootstrap.apply_saved_plan(namespace="unitnamespace", **_ADMIN)

    assert result == plan
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert any(
        call[1:3] == ("apply", "-input=false") and call[-1] == str(plan) for call in terraform_calls
    )
    assert any("-migrate-state" in call for call in terraform_calls)
    record = json.loads((operator_dir / "backend.json").read_text(encoding="utf-8"))
    assert record == {
        "auth": "SecurityToken",
        "bucket": "dander-phase7-state",
        "config_file_profile": "DANDER",
        "key": "dander/oci/bootstrap-admin/terraform.tfstate",
        "namespace": "unitnamespace",
        "region": "us-ashburn-1",
        "schema": "io.dander.oci-bootstrap-backend/v1",
        "tenancy_id": _TENANCY,
    }
    backend = json.loads(
        (operator_dir / "terraform-workspace/backend.tf.json").read_text(encoding="utf-8")
    )["terraform"]["backend"]["oci"]
    assert backend["auth"] == "SecurityToken"
    assert backend["bucket"] == "dander-phase7-state"
    assert "fingerprint" not in backend
    assert "private_key_path" not in backend
    assert "user_ocid" not in backend


def test_admin_repository_is_private_and_does_not_request_unsupported_immutability() -> None:
    configuration = (_ROOT / "infra/oci/bootstrap-admin/main.tf").read_text(encoding="utf-8")

    assert "is_public      = false" in configuration
    assert "is_immutable" not in configuration


def test_foundation_bootstrap_uses_native_backend_and_saved_plan(
    tmp_path: Path,
    terraform_calls: list[tuple[str, ...]],
) -> None:
    bootstrap = OciTerraformBootstrap(_ROOT / "infra/oci", tmp_path / "foundation")

    plan = bootstrap.execute(**cast("Any", _FOUNDATION))
    bootstrap.apply_saved_plan(**_FOUNDATION)

    init = terraform_calls[0]
    assert "-backend-config=auth=SecurityToken" in init
    assert "-backend-config=namespace=unitnamespace" in init
    assert "-backend-config=bucket=dander-phase7-state" in init
    assert terraform_calls[-1] == ("terraform", "apply", "-input=false", str(plan))
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600


def test_deployment_verification_reads_both_remote_states_and_requires_no_drift(
    tmp_path: Path,
    terraform_calls: list[tuple[str, ...]],
) -> None:
    admin_dir = tmp_path / "admin"
    admin = OciAdministrativeBootstrap(_ROOT / "infra/oci/bootstrap-admin", admin_dir)
    admin.execute(**_ADMIN)
    admin.apply_saved_plan(namespace="unitnamespace", **_ADMIN)
    foundation = OciTerraformBootstrap(_ROOT / "infra/oci", tmp_path / "foundation")

    admin.verify_no_drift(namespace="unitnamespace", **_ADMIN)
    foundation.verify_no_drift(**cast("Any", _FOUNDATION))

    verification_plans = [call for call in terraform_calls if "-detailed-exitcode" in call]
    assert len(verification_plans) == 2
    assert all("-input=false" in call for call in verification_plans)


def test_deployment_verification_fails_closed_on_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(args, 2 if "-detailed-exitcode" in args else 0)

    monkeypatch.setattr(subprocess, "run", run)
    foundation = OciTerraformBootstrap(_ROOT / "infra/oci", tmp_path / "foundation")

    with pytest.raises(OciTerraformBootstrapError, match="found Terraform drift"):
        foundation.verify_no_drift(**cast("Any", _FOUNDATION))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"tenancy_id": "not-an-ocid"}, "tenancy"),
        ({"state_key": "/absolute.tfstate"}, "state key"),
        ({"repository_name": "dander//runtime"}, "repository"),
        ({"config_file_profile": "profile with spaces"}, "SecurityToken profile"),
    ],
)
def test_admin_rejects_unsafe_inputs_before_terraform(
    tmp_path: Path,
    terraform_calls: list[tuple[str, ...]],
    update: dict[str, str],
    message: str,
) -> None:
    bootstrap = OciAdministrativeBootstrap(
        _ROOT / "infra/oci/bootstrap-admin",
        tmp_path / "operator",
    )

    with pytest.raises(OciTerraformBootstrapError, match=message):
        bootstrap.execute(**{**_ADMIN, **update})

    assert terraform_calls == []


def test_oci_bootstrap_rejects_repository_owned_operator_artifacts() -> None:
    with pytest.raises(OciTerraformBootstrapError, match="outside the repository"):
        OciTerraformBootstrap(_ROOT / "infra/oci", _ROOT / "tmp/oci-operator")


def test_foundation_apply_requires_reviewed_plan(
    tmp_path: Path,
    terraform_calls: list[tuple[str, ...]],
) -> None:
    bootstrap = OciTerraformBootstrap(_ROOT / "infra/oci", tmp_path / "operator")

    with pytest.raises(OciTerraformBootstrapError, match="Saved OCI plan is missing"):
        bootstrap.apply_saved_plan(**_FOUNDATION)

    assert terraform_calls == []
