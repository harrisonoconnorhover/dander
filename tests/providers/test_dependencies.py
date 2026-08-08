"""Public provider extras and full-runtime dependency assembly."""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

import dander.providers.dependencies as dependency_module
from dander.providers import (
    FULL_RUNTIME_DISTRIBUTIONS,
    PROVIDER_DEPENDENCY_SETS,
    RuntimeDependencyError,
    installed_runtime_dependencies,
    require_full_runtime,
)

_DISTRIBUTION = re.compile(r"^[A-Za-z0-9._-]+")


def _distribution(requirement: str) -> str:
    matched = _DISTRIBUTION.match(requirement)
    assert matched is not None
    return matched.group(0).lower().replace("_", "-")


def test_public_provider_extras_match_the_full_runtime_union() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project["optional-dependencies"]

    expected_names = {
        "bigquery",
        "snowflake",
        "redshift",
        "postgres",
        "gcp",
        "aws",
        "azure",
        "oci",
    }
    assert {dependency_set.extra for dependency_set in PROVIDER_DEPENDENCY_SETS} == expected_names
    for dependency_set in PROVIDER_DEPENDENCY_SETS:
        assert {_distribution(requirement) for requirement in extras[dependency_set.extra]} == set(
            dependency_set.distributions
        )
    assert {_distribution(requirement) for requirement in extras["runtime-all"]} == set(
        FULL_RUNTIME_DISTRIBUTIONS
    )


def test_dependency_inspection_uses_metadata_without_importing_provider_sdks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected_names: list[str] = []

    def version(distribution: str) -> str:
        inspected_names.append(distribution)
        return "1.2.3"

    monkeypatch.setattr(dependency_module, "_distribution_version", version)

    assert installed_runtime_dependencies() == {
        distribution: "1.2.3" for distribution in FULL_RUNTIME_DISTRIBUTIONS
    }
    assert tuple(inspected_names) == FULL_RUNTIME_DISTRIBUTIONS


def test_full_runtime_requirement_reports_only_missing_distribution_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def version(distribution: str) -> str:
        if distribution in {"azure-identity", "snowflake-connector-python"}:
            raise metadata.PackageNotFoundError(distribution)
        return "1.2.3"

    monkeypatch.setattr(dependency_module, "_distribution_version", version)

    with pytest.raises(RuntimeDependencyError) as raised:
        require_full_runtime()

    assert str(raised.value) == (
        "Full runtime is missing provider distributions: azure-identity, snowflake-connector-python"
    )


def test_full_runtime_requirement_returns_installed_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependency_module, "_distribution_version", lambda _name: "9.8.7")

    assert require_full_runtime() == {
        distribution: "9.8.7" for distribution in FULL_RUNTIME_DISTRIBUTIONS
    }
