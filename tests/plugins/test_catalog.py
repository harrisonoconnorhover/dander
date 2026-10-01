"""Curated connector-catalog behavior."""

from __future__ import annotations

from typing import Any, cast

import pytest

from dander.plugins import (
    CURATED_CONNECTORS,
    ConnectorPlugin,
    InstalledConnectorPlugin,
    build_plugin_catalog,
    search_connector_catalog,
)


def _installed_salesforce(version: str = "0.2.0") -> InstalledConnectorPlugin:
    return InstalledConnectorPlugin(
        plugin=ConnectorPlugin(
            plugin_id="salesforce",
            api_version=1,
            engine="salesforce_bulk2",
            display_name="Salesforce",
            source_factory=cast("Any", lambda *_args: None),
        ),
        distribution="dander-connector-salesforce",
        version=version,
    )


@pytest.mark.parametrize("version", ["0.7.0rc1", "0.7.1", "0.9.0rc20", "0.9.0rc33"])
def test_curated_catalog_has_exact_pins_and_current_dander_compatibility(version: str) -> None:
    catalog = build_plugin_catalog(dander_version=version)
    connectors = cast("list[dict[str, object]]", catalog["connectors"])

    assert catalog["schema_version"] == 1
    assert catalog["dander_version"] == version
    assert {connector["id"] for connector in connectors} == {"salesforce", "servicenow"}
    assert {connector["version"] for connector in connectors} == {"0.3.2", "0.2.3"}
    assert {connector["id"]: connector["dander_specifier"] for connector in connectors} == {
        "salesforce": ">=0.6.0rc1,<0.10",
        "servicenow": ">=0.5.0,<0.10",
    }
    assert all(connector["compatible"] is True for connector in connectors)
    assert all(connector["support_status"] == "first-party-beta" for connector in connectors)
    assert all(connector["validation_status"] == "provider-validated" for connector in connectors)
    assert all(
        str(connector["pypi_url"]).startswith("https://pypi.org/") for connector in connectors
    )


def test_catalog_search_is_case_insensitive_and_searches_capabilities() -> None:
    assert [entry.connector_id for entry in search_connector_catalog("SALES")] == ["salesforce"]
    assert [entry.connector_id for entry in search_connector_catalog("incident")] == ["servicenow"]
    assert search_connector_catalog("not-a-connector") == ()
    assert search_connector_catalog() == CURATED_CONNECTORS


def test_catalog_installation_status_uses_only_validated_manifest_plugins() -> None:
    catalog = build_plugin_catalog(
        (_installed_salesforce("0.1.0"),),
        dander_version="0.5.0",
    )
    connectors = {
        connector["id"]: connector
        for connector in cast("list[dict[str, object]]", catalog["connectors"])
    }

    assert connectors["salesforce"]["installed"] is True
    assert connectors["salesforce"]["installed_version"] == "0.1.0"
    assert connectors["servicenow"]["installed"] is False
    assert connectors["servicenow"]["installed_version"] is None


def test_catalog_marks_unsupported_dander_version_incompatible() -> None:
    catalog = build_plugin_catalog(dander_version="0.10.0")
    connectors = cast("list[dict[str, object]]", catalog["connectors"])

    assert all(connector["compatible"] is False for connector in connectors)


@pytest.mark.parametrize(
    ("version", "salesforce", "servicenow"),
    [("0.4.9", False, False), ("0.5.0", False, True), ("0.6.0rc1", True, True)],
)
def test_catalog_preserves_each_published_minimum(
    version: str, salesforce: bool, servicenow: bool
) -> None:
    catalog = build_plugin_catalog(dander_version=version)
    connectors = cast("list[dict[str, object]]", catalog["connectors"])

    assert {connector["id"]: connector["compatible"] for connector in connectors} == {
        "salesforce": salesforce,
        "servicenow": servicenow,
    }
