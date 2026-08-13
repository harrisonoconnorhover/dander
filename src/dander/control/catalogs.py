"""Presentation-safe catalog projections shared by local and hosted Control APIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dander.control.models import (
    ConnectorCatalogResponse,
    OperationCatalogResponse,
    PluginCatalogResponse,
)
from dander.pipeline.operations import build_operation_catalog
from dander.plugins.catalog import build_plugin_catalog

if TYPE_CHECKING:
    from dander.plugins import InstalledConnectorPlugin


def build_connector_catalog(
    plugins: tuple[InstalledConnectorPlugin, ...] = (),
) -> ConnectorCatalogResponse:
    """Project installed connectors without configuration, credentials, or provider payloads."""
    connectors: list[dict[str, object]] = []
    for installed in plugins:
        plugin = installed.plugin
        for connector in plugin.connectors:
            connectors.append(
                {
                    "id": connector.connector_id,
                    "display_name": connector.display_name,
                    "engine": connector.engine,
                    "description": connector.description,
                    "plugin": {
                        "id": plugin.plugin_id,
                        "distribution": installed.distribution,
                        "version": installed.version,
                    },
                    "endpoints": [
                        {
                            "id": endpoint.endpoint_id,
                            "display_name": endpoint.display_name,
                            "graph_binding": {
                                "connector": connector.connector_id,
                                "endpoint": endpoint.endpoint_id,
                            },
                            "fields": [
                                {
                                    "name": field.name,
                                    "display_name": field.display_name,
                                    "data_type": field.data_type,
                                    "required": field.required,
                                }
                                for field in endpoint.fields
                            ],
                        }
                        for endpoint in connector.endpoints
                    ],
                }
            )
    return ConnectorCatalogResponse.model_validate({"connectors": connectors})


def build_typed_plugin_catalog(
    plugins: tuple[InstalledConnectorPlugin, ...] = (),
) -> PluginCatalogResponse:
    """Return the existing curated plugin catalog through its public transport DTO."""
    return PluginCatalogResponse.model_validate(build_plugin_catalog(plugins))


def build_typed_operation_catalog() -> OperationCatalogResponse:
    """Return Dander's canonical operation descriptors through their public DTO."""
    return OperationCatalogResponse.model_validate(build_operation_catalog())


__all__ = [
    "build_connector_catalog",
    "build_typed_operation_catalog",
    "build_typed_plugin_catalog",
]
