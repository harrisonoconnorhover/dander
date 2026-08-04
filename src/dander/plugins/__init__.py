"""Stable connector-plugin API and explicit manifest-driven discovery."""

from dander.plugins.contracts import (
    PLUGIN_API_VERSION,
    ConnectorDescriptor,
    ConnectorEndpointDescriptor,
    ConnectorFieldDescriptor,
    ConnectorPlugin,
    SourceFactory,
)
from dander.plugins.registry import (
    ENTRY_POINT_GROUP,
    ConnectorPluginError,
    ConnectorPluginRegistry,
    InstalledConnectorPlugin,
    load_connector_plugins,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "ConnectorDescriptor",
    "ConnectorEndpointDescriptor",
    "ConnectorFieldDescriptor",
    "ConnectorPlugin",
    "ConnectorPluginError",
    "ConnectorPluginRegistry",
    "ENTRY_POINT_GROUP",
    "InstalledConnectorPlugin",
    "SourceFactory",
    "load_connector_plugins",
]
