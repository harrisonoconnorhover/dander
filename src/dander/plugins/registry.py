"""Discovery and validation for explicitly pinned connector plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata as metadata
from typing import TYPE_CHECKING, Protocol

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from dander.ingestion import (
    DltRestSource,
    IngestionEngine,
    NetSuiteSuiteQLSource,
    OdooJson2Source,
    SalesforceBulk2Source,
    Source,
    SourceCapabilities,
    WorkdayRaasSource,
)
from dander.plugins.contracts import (
    PLUGIN_API_VERSION,
    ConnectorDescriptor,
    ConnectorEndpointDescriptor,
    ConnectorFieldDescriptor,
    ConnectorPlugin,
    SourceFactory,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.ingestion import SourceConfig
    from dander.security import AuthStrategy

ENTRY_POINT_GROUP = "dander.connectors"
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class PluginPin(Protocol):
    """Structural view of one exact manifest plugin pin."""

    @property
    def distribution(self) -> str: ...

    @property
    def version(self) -> str: ...


class ConnectorPluginError(ValueError):
    """Raised when an explicitly declared connector plugin cannot be used safely."""


@dataclass(frozen=True, slots=True)
class InstalledConnectorPlugin:
    """A validated plugin plus the package identity that supplied it."""

    plugin: ConnectorPlugin
    distribution: str
    version: str


class ConnectorPluginRegistry:
    """Resolved built-in and explicitly declared source factories."""

    def __init__(
        self,
        *,
        source_factories: Mapping[str, SourceFactory],
        plugins: tuple[InstalledConnectorPlugin, ...],
    ) -> None:
        self._source_factories = dict(source_factories)
        self.plugins = plugins

    @property
    def engines(self) -> tuple[str, ...]:
        """Return every active engine key in deterministic order."""
        return tuple(sorted(self._source_factories))

    def require_engine(self, engine: IngestionEngine | str) -> SourceFactory:
        """Return an engine factory or raise a configuration-facing error."""
        engine_key = engine.value if isinstance(engine, IngestionEngine) else engine
        try:
            return self._source_factories[engine_key]
        except KeyError as error:
            raise ConnectorPluginError(
                f"Unknown ingestion engine {engine_key!r}; declare and install its connector "
                "plugin in dander.yaml"
            ) from error

    def build_source(
        self,
        config: SourceConfig,
        auth: AuthStrategy,
    ) -> Source:
        """Build a source and verify that the plugin factory honored Dander's contract."""
        engine_key = (
            config.engine.value if isinstance(config.engine, IngestionEngine) else config.engine
        )
        source = self.require_engine(engine_key)(config, auth)
        if not isinstance(source, Source):
            raise ConnectorPluginError(
                f"Source factory for engine {engine_key!r} returned {type(source).__name__}, "
                "not dander.ingestion.Source"
            )
        return source

    def build_capabilities(
        self,
        config: SourceConfig,
        auth: AuthStrategy,
    ) -> SourceCapabilities:
        """Build a source and expose the optional read operations it actually implements."""
        return SourceCapabilities(self.build_source(config, auth))


def load_connector_plugins(
    pins: Mapping[str, PluginPin],
) -> ConnectorPluginRegistry:
    """Load only manifest-declared entry points and combine them with built-in engines."""
    factories = _builtin_source_factories()
    installed_plugins: list[InstalledConnectorPlugin] = []
    declared_distributions: set[str] = set()
    plugin_engines: dict[str, str] = {}

    for declared_id, pin in sorted(pins.items()):
        distribution_name = canonicalize_name(pin.distribution)
        if distribution_name in declared_distributions:
            raise ConnectorPluginError(
                f"Plugin distribution {pin.distribution!r} is declared more than once"
            )
        declared_distributions.add(distribution_name)
        try:
            distribution = metadata.distribution(pin.distribution)
        except metadata.PackageNotFoundError as error:
            raise ConnectorPluginError(
                f"Plugin {declared_id!r} requires missing package "
                f"{pin.distribution}=={pin.version}; run 'dander plugins install'"
            ) from error

        try:
            installed_version = Version(distribution.version)
            required_version = Version(pin.version)
        except InvalidVersion as error:
            raise ConnectorPluginError(
                f"Plugin {declared_id!r} has invalid installed or required version metadata"
            ) from error
        if installed_version != required_version:
            raise ConnectorPluginError(
                f"Plugin {declared_id!r} requires {pin.distribution}=={pin.version}, "
                f"but {distribution.version} is installed"
            )

        entry_points = [
            entry_point
            for entry_point in distribution.entry_points
            if entry_point.group == ENTRY_POINT_GROUP and entry_point.name == declared_id
        ]
        if len(entry_points) != 1:
            raise ConnectorPluginError(
                f"Package {pin.distribution}=={pin.version} must expose exactly one "
                f"{ENTRY_POINT_GROUP} entry point named {declared_id!r}"
            )
        try:
            plugin_factory = entry_points[0].load()
            if not callable(plugin_factory):
                raise TypeError("entry point is not callable")
            plugin = plugin_factory()
        except Exception as error:
            raise ConnectorPluginError(
                f"Plugin {declared_id!r} entry-point factory could not be loaded"
            ) from error
        validate_connector_plugin(plugin, expected_plugin_id=declared_id)

        if previous := plugin_engines.get(plugin.engine):
            raise ConnectorPluginError(
                f"Plugins {previous!r} and {plugin.plugin_id!r} both register engine "
                f"{plugin.engine!r}"
            )
        plugin_engines[plugin.engine] = plugin.plugin_id
        factories[plugin.engine] = plugin.source_factory
        installed_plugins.append(
            InstalledConnectorPlugin(
                plugin=plugin,
                distribution=pin.distribution,
                version=pin.version,
            )
        )

    return ConnectorPluginRegistry(
        source_factories=factories,
        plugins=tuple(installed_plugins),
    )


def validate_connector_plugin(
    plugin: object,
    *,
    expected_plugin_id: str,
) -> ConnectorPlugin:
    """Validate one API-v1 plugin declaration and return its narrowed value."""
    if not isinstance(plugin, ConnectorPlugin):
        raise ConnectorPluginError(
            f"Plugin {expected_plugin_id!r} factory must return dander.plugins.ConnectorPlugin"
        )
    if plugin.plugin_id != expected_plugin_id or not _PLUGIN_ID.fullmatch(plugin.plugin_id):
        raise ConnectorPluginError(
            f"Plugin entry point {expected_plugin_id!r} returned plugin ID {plugin.plugin_id!r}"
        )
    if plugin.api_version != PLUGIN_API_VERSION:
        raise ConnectorPluginError(
            f"Plugin {expected_plugin_id!r} uses API version {plugin.api_version}; "
            f"Dander requires {PLUGIN_API_VERSION}"
        )
    if not _PLUGIN_ID.fullmatch(plugin.engine):
        raise ConnectorPluginError(
            f"Plugin {expected_plugin_id!r} has invalid engine key {plugin.engine!r}"
        )
    if not plugin.display_name.strip():
        raise ConnectorPluginError(f"Plugin {expected_plugin_id!r} must have a display name")
    if not callable(plugin.source_factory):
        raise ConnectorPluginError(f"Plugin {expected_plugin_id!r} source factory is not callable")
    _validate_descriptors(plugin)
    return plugin


def _validate_descriptors(plugin: ConnectorPlugin) -> None:
    connector_ids: set[str] = set()
    for connector in plugin.connectors:
        if not isinstance(connector, ConnectorDescriptor):
            raise ConnectorPluginError(
                f"Plugin {plugin.plugin_id!r} contains an invalid connector descriptor"
            )
        if connector.connector_id in connector_ids:
            raise ConnectorPluginError(
                f"Plugin {plugin.plugin_id!r} repeats connector {connector.connector_id!r}"
            )
        connector_ids.add(connector.connector_id)
        if not _PLUGIN_ID.fullmatch(connector.connector_id):
            raise ConnectorPluginError(
                f"Plugin {plugin.plugin_id!r} has invalid connector ID {connector.connector_id!r}"
            )
        if connector.engine != plugin.engine:
            raise ConnectorPluginError(
                f"Connector {connector.connector_id!r} must use plugin engine {plugin.engine!r}"
            )
        endpoint_ids: set[str] = set()
        for endpoint in connector.endpoints:
            if not isinstance(endpoint, ConnectorEndpointDescriptor):
                raise ConnectorPluginError(
                    f"Connector {connector.connector_id!r} has an invalid endpoint descriptor"
                )
            if endpoint.endpoint_id in endpoint_ids:
                raise ConnectorPluginError(
                    f"Connector {connector.connector_id!r} repeats endpoint "
                    f"{endpoint.endpoint_id!r}"
                )
            endpoint_ids.add(endpoint.endpoint_id)
            for field in endpoint.fields:
                if not isinstance(field, ConnectorFieldDescriptor):
                    raise ConnectorPluginError(
                        f"Endpoint {endpoint.endpoint_id!r} has an invalid field descriptor"
                    )


def _builtin_source_factories() -> dict[str, SourceFactory]:
    return {
        IngestionEngine.DLT.value: DltRestSource,
        IngestionEngine.NETSUITE_SUITEQL.value: NetSuiteSuiteQLSource,
        IngestionEngine.ODOO_JSON2.value: OdooJson2Source,
        IngestionEngine.SALESFORCE_BULK2.value: SalesforceBulk2Source,
        IngestionEngine.WORKDAY_RAAS.value: WorkdayRaasSource,
    }
