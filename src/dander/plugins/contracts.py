"""Public connector-plugin contract for independently distributed source adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from dander.ingestion import Source, SourceConfig
from dander.security import AuthStrategy

PLUGIN_API_VERSION = 1

type SourceFactory = Callable[[SourceConfig, AuthStrategy], Source]


@dataclass(frozen=True, slots=True)
class ConnectorFieldDescriptor:
    """Non-secret field metadata used to present a connector in authoring tools."""

    name: str
    display_name: str
    data_type: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class ConnectorEndpointDescriptor:
    """One endpoint exposed by a connector plugin."""

    endpoint_id: str
    display_name: str
    fields: tuple[ConnectorFieldDescriptor, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ConnectorDescriptor:
    """Presentation-safe description of one connector configuration shape."""

    connector_id: str
    display_name: str
    engine: str
    description: str = ""
    endpoints: tuple[ConnectorEndpointDescriptor, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ConnectorPlugin:
    """The value returned by a ``dander.connectors`` entry-point factory."""

    plugin_id: str
    api_version: int
    engine: str
    display_name: str
    source_factory: SourceFactory
    description: str = ""
    connectors: tuple[ConnectorDescriptor, ...] = field(default_factory=tuple)
