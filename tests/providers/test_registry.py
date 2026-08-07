"""Provider registry selection, validation, and lazy-loading tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import BaseModel, ConfigDict

from dander.providers import (
    PROVIDER_API_VERSION,
    ProviderFactory,
    ProviderFactoryError,
    ProviderKind,
    ProviderRegistry,
    lazy_provider_factory,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pytest import MonkeyPatch


class ExampleWarehouseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["example"]
    namespace: str


def _factory(*, api_version: int = PROVIDER_API_VERSION) -> ProviderFactory[object]:
    def build(config: BaseModel, context: Mapping[str, object]) -> object:
        assert isinstance(config, ExampleWarehouseConfig)
        return {
            "namespace": config.namespace,
            "client": context.get("client"),
        }

    return ProviderFactory(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        api_version=api_version,
        build=build,
    )


def test_registry_parses_then_lazily_builds_only_selected_provider() -> None:
    loads = 0

    def load() -> ProviderFactory[object]:
        nonlocal loads
        loads += 1
        return _factory()

    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        config_model=ExampleWarehouseConfig,
        load_factory=load,
    )

    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {"provider": "example", "namespace": "analytics"},
    )
    assert loads == 0
    assert registry.providers(ProviderKind.WAREHOUSE) == ("example",)
    assert registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={"client": "injected"},
    ) == {"namespace": "analytics", "client": "injected"}
    second = registry.build(ProviderKind.WAREHOUSE, config)
    assert isinstance(second, dict)
    assert second["namespace"] == "analytics"
    assert loads == 1


@pytest.mark.parametrize("kind", list(ProviderKind))
def test_every_provider_category_uses_the_same_registry_contract(kind: ProviderKind) -> None:
    class CategoryConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")

        provider: Literal["example"]

    registry = ProviderRegistry()
    registry.register(
        kind=kind,
        provider_id="example",
        config_model=CategoryConfig,
        load_factory=lambda: ProviderFactory(
            kind=kind,
            provider_id="example",
            api_version=PROVIDER_API_VERSION,
            build=lambda _config, _context: kind.value,
        ),
    )

    config = registry.parse(kind, {"provider": "example"})
    assert registry.build(kind, config) == kind.value


def test_registry_rejects_duplicates_unknowns_and_invalid_config_without_values() -> None:
    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        config_model=ExampleWarehouseConfig,
        load_factory=_factory,
    )

    with pytest.raises(ProviderFactoryError, match="registered more than once"):
        registry.register(
            kind=ProviderKind.WAREHOUSE,
            provider_id="example",
            config_model=ExampleWarehouseConfig,
            load_factory=_factory,
        )
    with pytest.raises(ProviderFactoryError, match="Unknown state provider"):
        registry.parse(ProviderKind.STATE, {"provider": "missing"})
    with pytest.raises(ProviderFactoryError, match="check: namespace") as captured:
        registry.parse(ProviderKind.WAREHOUSE, {"provider": "example"})
    assert "analytics" not in str(captured.value)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (_factory(api_version=2), "uses API version 2"),
        (
            ProviderFactory(
                kind=ProviderKind.STATE,
                provider_id="example",
                api_version=PROVIDER_API_VERSION,
                build=lambda _config, _context: object(),
            ),
            "identity does not match",
        ),
    ],
)
def test_registry_rejects_incompatible_factory(
    factory: ProviderFactory[object],
    message: str,
) -> None:
    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        config_model=ExampleWarehouseConfig,
        load_factory=lambda: factory,
    )
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {"provider": "example", "namespace": "analytics"},
    )

    with pytest.raises(ProviderFactoryError, match=message):
        registry.build(ProviderKind.WAREHOUSE, config)


def test_lazy_import_path_loads_only_when_selected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    module_path = tmp_path / "example_provider.py"
    module_path.write_text(
        "from dander.providers import PROVIDER_API_VERSION, ProviderFactory, ProviderKind\n"
        "FACTORY = ProviderFactory(\n"
        "    kind=ProviderKind.WAREHOUSE, provider_id='example',\n"
        "    api_version=PROVIDER_API_VERSION,\n"
        "    build=lambda config, context: config.namespace,\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        config_model=ExampleWarehouseConfig,
        load_factory=lazy_provider_factory("example_provider:FACTORY"),
    )
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {"provider": "example", "namespace": "analytics"},
    )

    assert registry.build(ProviderKind.WAREHOUSE, config) == "analytics"


def test_registry_rejects_config_model_from_another_provider() -> None:
    class OtherConfig(BaseModel):
        provider: Literal["example"]

    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="example",
        config_model=ExampleWarehouseConfig,
        load_factory=_factory,
    )

    with pytest.raises(ProviderFactoryError, match="wrong configuration type"):
        registry.build(ProviderKind.WAREHOUSE, OtherConfig(provider="example"))
