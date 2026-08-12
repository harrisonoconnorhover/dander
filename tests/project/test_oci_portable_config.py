"""Named OCI profile selection and unsupported-combination rejection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from pathlib import Path

_TENANCY_OCID = "ocid1.tenancy.oc1.." + "a" * 32
_COMPARTMENT_OCID = "ocid1.compartment.oc1.." + "b" * 32
_SUBNET_OCID = "ocid1.subnet.oc1.iad." + "c" * 32
_VAULT_OCID = "ocid1.vault.oc1.iad.liveprovidersegment." + "d" * 32


def _documents() -> tuple[dict[str, object], dict[str, object]]:
    logical: dict[str, object] = {
        "version": 2,
        "pipelines": {
            "warehouse_fixture": {
                "source": "warehouse_fixture",
                "models": ["fixture"],
            }
        },
    }
    platforms: dict[str, object] = {
        "version": 1,
        "platforms": {
            "oci_postgresql": {
                "warehouse": {
                    "provider": "postgresql",
                    "database": "dander",
                    "schema": "raw",
                },
                "state": {
                    "provider": "postgresql",
                    "authority_id": "postgresql:oci-phase7",
                },
                "catalog": {"provider": "none"},
                "secrets": {"provider": "oci_vault"},
            }
        },
        "deployments": {
            "oci_container_instances": {
                "platform": "oci_postgresql",
                "launcher": {
                    "provider": "oci_container_instances",
                    "region": "us-ashburn-1",
                    "tenancy_id": _TENANCY_OCID,
                    "compartment_id": _COMPARTMENT_OCID,
                    "availability_domain": "Unit:US-ASHBURN-AD-1",
                    "subnet_id": _SUBNET_OCID,
                    "registry_namespace": "unitnamespace",
                    "repository_name": "dander/runtime",
                    "vault_id": _VAULT_OCID,
                    "dynamic_group_name": "dander_phase7_runtime",
                },
                "runtime": {
                    "cpu": 1,
                    "memory": "2Gi",
                    "timeout_seconds": 900,
                    "max_retries": 1,
                },
                "safety": {"require_guarded_free_tier": False},
                "pipelines": {
                    "warehouse_fixture": {
                        "schedule": "15 4 * * *",
                        "time_zone": "UTC",
                        "paused": True,
                        "secret_bindings": {"DANDER_POSTGRES_DSN": "postgres-dsn"},
                    }
                },
            }
        },
    }
    return logical, platforms


def _write_documents(
    tmp_path: Path,
    *,
    platforms: dict[str, object],
    logical: dict[str, object],
) -> Path:
    project_path = tmp_path / "dander.yaml"
    project_path.write_text(yaml.safe_dump(logical), encoding="utf-8")
    (tmp_path / "dander.platforms.yaml").write_text(
        yaml.safe_dump(platforms),
        encoding="utf-8",
    )
    return project_path


def test_version_two_resolves_complete_oci_postgresql_profile(tmp_path: Path) -> None:
    logical, platforms = _documents()
    resolved = load_project_config(
        _write_documents(tmp_path, platforms=platforms, logical=logical),
        deployment="oci_container_instances",
    )

    assert resolved.platform_name == "oci_postgresql"
    assert resolved.warehouse_provider == "postgresql"
    assert resolved.state_provider == "postgresql"
    assert resolved.catalog_provider == "none"
    assert resolved.secret_provider == "oci_vault"
    assert resolved.launcher_provider == "oci_container_instances"
    assert resolved.resolved_launcher_config()["vault_id"] == _VAULT_OCID
    assert resolved.platform.runtime.memory == "2Gi"


@pytest.mark.parametrize(
    ("profile_update", "launcher_provider", "message"),
    [
        (
            {"warehouse": {"provider": "bigquery", "location": "US"}},
            "oci_container_instances",
            "named PostgreSQL",
        ),
        ({}, "cloud_run", "OCI Vault projection"),
    ],
)
def test_oci_profile_fails_closed_on_unsupported_combinations(
    tmp_path: Path,
    profile_update: dict[str, object],
    launcher_provider: str,
    message: str,
) -> None:
    logical, platforms = _documents()
    profiles = platforms["platforms"]
    assert isinstance(profiles, dict)
    profile = profiles["oci_postgresql"]
    assert isinstance(profile, dict)
    profile.update(profile_update)
    deployments = platforms["deployments"]
    assert isinstance(deployments, dict)
    deployment = deployments["oci_container_instances"]
    assert isinstance(deployment, dict)
    launcher = deployment["launcher"]
    assert isinstance(launcher, dict)
    if launcher_provider == "cloud_run":
        deployment["launcher"] = {
            "provider": "cloud_run",
            "region": "us-central1",
        }

    with pytest.raises(ProjectConfigError, match=message):
        load_project_config(
            _write_documents(tmp_path, platforms=platforms, logical=logical),
            deployment="oci_container_instances",
        )
