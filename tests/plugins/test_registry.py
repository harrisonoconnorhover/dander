"""Manifest-scoped connector-plugin discovery and engine precedence."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any

import pytest

import dander.plugins.registry as registry_module
from dander.ingestion import (
    ConnectionStatus,
    ConnectorOperation,
    IngestionEngine,
    Source,
    SourceConfig,
)
from dander.plugins import (
    PLUGIN_API_VERSION,
    ConnectorDescriptor,
    ConnectorPlugin,
    ConnectorPluginError,
    load_connector_plugins,
)
from dander.project import PluginSpec
from dander.security import AuthStrategy, NoAuth

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class _PluginSource(Source):
    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(
        self,
        endpoint: str,
        *,
        since: str | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        del endpoint, since
        return iter(())


class _CapablePluginSource(_PluginSource):
    def test_connection(self) -> ConnectionStatus:
        return ConnectionStatus(ok=True)


def _plugin_source_factory(config: SourceConfig, auth: AuthStrategy) -> Source:
    del auth
    return _PluginSource(config)


def _capable_plugin_source_factory(config: SourceConfig, auth: AuthStrategy) -> Source:
    del auth
    return _CapablePluginSource(config)


@dataclass(frozen=True)
class _EntryPoint:
    name: str
    plugin: object
    group: str = "dander.connectors"

    def load(self) -> object:
        return lambda: self.plugin


@dataclass(frozen=True)
class _Distribution:
    version: str
    entry_points: tuple[_EntryPoint, ...]


def _plugin(
    plugin_id: str,
    engine: str,
    *,
    api_version: int = PLUGIN_API_VERSION,
    source_factory: object = _plugin_source_factory,
) -> ConnectorPlugin:
    return ConnectorPlugin(
        plugin_id=plugin_id,
        api_version=api_version,
        engine=engine,
        display_name=plugin_id.title(),
        source_factory=source_factory,  # type: ignore[arg-type]
        connectors=(
            ConnectorDescriptor(
                connector_id=plugin_id,
                display_name=plugin_id.title(),
                engine=engine,
            ),
        ),
    )


def _patch_distributions(
    monkeypatch: pytest.MonkeyPatch,
    distributions: Mapping[str, _Distribution],
) -> None:
    def distribution(name: str) -> _Distribution:
        try:
            return distributions[name]
        except KeyError as error:
            raise metadata.PackageNotFoundError(name) from error

    monkeypatch.setattr(registry_module.metadata, "distribution", distribution)


def test_declared_plugin_overrides_matching_builtin_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    salesforce = _plugin("salesforce", IngestionEngine.SALESFORCE_BULK2.value)
    _patch_distributions(
        monkeypatch,
        {
            "dander-connector-salesforce": _Distribution(
                version="0.1.0",
                entry_points=(_EntryPoint(name="salesforce", plugin=salesforce),),
            )
        },
    )

    registry = load_connector_plugins(
        {
            "salesforce": PluginSpec(
                distribution="dander-connector-salesforce",
                version="0.1.0",
            )
        }
    )
    source = registry.build_source(
        SourceConfig(
            name="salesforce",
            base_url="https://example.test",
            engine="salesforce_bulk2",
            auth_strategy="none",
        ),
        NoAuth(),
    )

    assert isinstance(source, _PluginSource)
    assert registry.plugins[0].distribution == "dander-connector-salesforce"


def test_declared_plugin_source_exposes_structural_capabilities_without_api_v1_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(
        "example",
        "example_engine",
        source_factory=_capable_plugin_source_factory,
    )
    _patch_distributions(
        monkeypatch,
        {
            "dander-connector-example": _Distribution(
                version="0.1.0",
                entry_points=(_EntryPoint(name="example", plugin=plugin),),
            )
        },
    )
    registry = load_connector_plugins(
        {
            "example": PluginSpec(
                distribution="dander-connector-example",
                version="0.1.0",
            )
        }
    )

    capabilities = registry.build_capabilities(
        SourceConfig(
            name="example",
            base_url="https://example.test",
            engine="example_engine",
            auth_strategy="none",
        ),
        NoAuth(),
    )

    assert capabilities.supported_operations == {
        ConnectorOperation.TEST_CONNECTION,
    }
    assert capabilities.test_connection() == ConnectionStatus(ok=True)


def test_undeclared_distributions_are_never_inspected(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_distribution(name: str) -> _Distribution:
        raise AssertionError(f"unexpected package lookup: {name}")

    monkeypatch.setattr(registry_module.metadata, "distribution", unexpected_distribution)

    registry = load_connector_plugins({})

    assert IngestionEngine.DLT.value in registry.engines


@pytest.mark.parametrize(
    ("installed_version", "api_version", "match"),
    [
        (None, PLUGIN_API_VERSION, "missing package"),
        ("0.2.0", PLUGIN_API_VERSION, "but 0.2.0 is installed"),
        ("0.1.0", 2, "uses API version 2"),
    ],
)
def test_missing_wrong_version_and_incompatible_plugins_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
    installed_version: str | None,
    api_version: int,
    match: str,
) -> None:
    distributions = (
        {}
        if installed_version is None
        else {
            "dander-connector-example": _Distribution(
                version=installed_version,
                entry_points=(
                    _EntryPoint(
                        name="example",
                        plugin=_plugin("example", "example_engine", api_version=api_version),
                    ),
                ),
            )
        }
    )
    _patch_distributions(monkeypatch, distributions)

    with pytest.raises(ConnectorPluginError, match=match):
        load_connector_plugins(
            {
                "example": PluginSpec(
                    distribution="dander-connector-example",
                    version="0.1.0",
                )
            }
        )


def test_duplicate_plugin_engines_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_distributions(
        monkeypatch,
        {
            "dander-connector-one": _Distribution(
                version="0.1.0",
                entry_points=(_EntryPoint(name="one", plugin=_plugin("one", "shared")),),
            ),
            "dander-connector-two": _Distribution(
                version="0.1.0",
                entry_points=(_EntryPoint(name="two", plugin=_plugin("two", "shared")),),
            ),
        },
    )

    with pytest.raises(ConnectorPluginError, match="both register engine 'shared'"):
        load_connector_plugins(
            {
                "one": PluginSpec(distribution="dander-connector-one", version="0.1.0"),
                "two": PluginSpec(distribution="dander-connector-two", version="0.1.0"),
            }
        )


def test_unknown_engine_and_invalid_factory_result_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _plugin("invalid", "invalid_engine", source_factory=lambda *_: object())
    _patch_distributions(
        monkeypatch,
        {
            "dander-connector-invalid": _Distribution(
                version="0.1.0",
                entry_points=(_EntryPoint(name="invalid", plugin=invalid),),
            )
        },
    )
    registry = load_connector_plugins(
        {
            "invalid": PluginSpec(
                distribution="dander-connector-invalid",
                version="0.1.0",
            )
        }
    )

    with pytest.raises(ConnectorPluginError, match="not dander.ingestion.Source"):
        registry.build_source(
            SourceConfig(
                name="invalid",
                base_url="https://example.test",
                engine="invalid_engine",
                auth_strategy="none",
            ),
            NoAuth(),
        )
    with pytest.raises(ConnectorPluginError, match="Unknown ingestion engine 'other'"):
        registry.require_engine("other")
