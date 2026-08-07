"""Typed, lazy provider-factory registration for platform adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import TypeVar

from pydantic import BaseModel, ValidationError

PROVIDER_API_VERSION = 1
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_IMPORT_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


class ProviderKind(StrEnum):
    """Independent platform capabilities selected by one deployment profile."""

    WAREHOUSE = "warehouse"
    STATE = "state"
    CATALOG = "catalog"
    SECRETS = "secrets"
    LAUNCHER = "launcher"


class ProviderFactoryError(ValueError):
    """A provider registration, configuration, or factory is invalid."""


ProviderProductT = TypeVar("ProviderProductT")
ProviderBuilder = Callable[[BaseModel, Mapping[str, object]], ProviderProductT]


@dataclass(frozen=True, slots=True)
class ProviderFactory[ProviderProductT]:
    """One validated API-v1 provider builder loaded only after selection."""

    kind: ProviderKind
    provider_id: str
    api_version: int
    build: ProviderBuilder[ProviderProductT]


ProviderFactoryLoader = Callable[[], ProviderFactory[object]]


@dataclass(frozen=True, slots=True)
class _Registration:
    config_model: type[BaseModel]
    load_factory: ProviderFactoryLoader


class ProviderRegistry:
    """Explicit provider configurations plus lazy category-specific factories.

    Registration imports only the small configuration model. The provider module and
    its SDK dependencies are loaded when ``build`` selects that exact provider.
    """

    def __init__(self) -> None:
        self._registrations: dict[tuple[ProviderKind, str], _Registration] = {}
        self._factories: dict[tuple[ProviderKind, str], ProviderFactory[object]] = {}

    def register(
        self,
        *,
        kind: ProviderKind,
        provider_id: str,
        config_model: type[BaseModel],
        load_factory: ProviderFactoryLoader,
    ) -> None:
        """Register one provider exactly once without loading its implementation."""
        _validate_provider_id(provider_id)
        if not isinstance(config_model, type) or not issubclass(config_model, BaseModel):
            raise ProviderFactoryError("provider config_model must be a Pydantic model")
        if not callable(load_factory):
            raise ProviderFactoryError("provider factory loader must be callable")
        key = (kind, provider_id)
        if key in self._registrations:
            raise ProviderFactoryError(
                f"Provider {kind.value}.{provider_id} is registered more than once"
            )
        self._registrations[key] = _Registration(
            config_model=config_model,
            load_factory=load_factory,
        )

    def providers(self, kind: ProviderKind) -> tuple[str, ...]:
        """List registered providers for one capability in deterministic order."""
        return tuple(
            sorted(
                provider_id
                for registered_kind, provider_id in self._registrations
                if registered_kind is kind
            )
        )

    def parse(self, kind: ProviderKind, raw: Mapping[str, object]) -> BaseModel:
        """Validate one explicitly selected provider block without importing its SDK."""
        provider_id = raw.get("provider")
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ProviderFactoryError(f"{kind.value}.provider must be a valid provider id")
        registration = self._registration(kind, provider_id)
        try:
            return registration.config_model.model_validate(dict(raw))
        except ValidationError as error:
            locations = sorted(
                {
                    ".".join(str(part) for part in issue["loc"]) or "<root>"
                    for issue in error.errors()
                }
            )
            raise ProviderFactoryError(
                f"Invalid {kind.value} provider {provider_id!r}; check: {', '.join(locations)}"
            ) from error

    def build(
        self,
        kind: ProviderKind,
        config: BaseModel,
        *,
        context: Mapping[str, object] | None = None,
    ) -> object:
        """Build only the selected provider with explicitly injected dependencies."""
        provider_id = getattr(config, "provider", None)
        if not isinstance(provider_id, str):
            raise ProviderFactoryError("provider config is missing its provider id")
        registration = self._registration(kind, provider_id)
        if not isinstance(config, registration.config_model):
            raise ProviderFactoryError(
                f"Provider {kind.value}.{provider_id} received the wrong configuration type"
            )
        key = (kind, provider_id)
        factory = self._factories.get(key)
        if factory is None:
            try:
                loaded = registration.load_factory()
            except Exception as error:
                raise ProviderFactoryError(
                    f"Provider {kind.value}.{provider_id} factory could not be loaded"
                ) from error
            factory = _validate_factory(loaded, kind=kind, provider_id=provider_id)
            self._factories[key] = factory
        return factory.build(config, context or {})

    def _registration(self, kind: ProviderKind, provider_id: str) -> _Registration:
        try:
            return self._registrations[(kind, provider_id)]
        except KeyError as error:
            available = ", ".join(self.providers(kind)) or "none"
            raise ProviderFactoryError(
                f"Unknown {kind.value} provider {provider_id!r}; available: {available}"
            ) from error


def lazy_provider_factory(import_path: str) -> ProviderFactoryLoader:
    """Create a loader for ``module:attribute`` without importing the module now."""
    if not _IMPORT_PATH.fullmatch(import_path):
        raise ProviderFactoryError("provider factory import path must use module:attribute")
    module_name, attribute = import_path.split(":", maxsplit=1)

    def load() -> ProviderFactory[object]:
        value = getattr(import_module(module_name), attribute)
        if not isinstance(value, ProviderFactory):
            raise TypeError("provider factory export has the wrong type")
        return value

    return load


def _validate_factory(
    value: object,
    *,
    kind: ProviderKind,
    provider_id: str,
) -> ProviderFactory[object]:
    if not isinstance(value, ProviderFactory):
        raise ProviderFactoryError(
            f"Provider {kind.value}.{provider_id} loader returned an invalid factory"
        )
    if value.api_version != PROVIDER_API_VERSION:
        raise ProviderFactoryError(
            f"Provider {kind.value}.{provider_id} uses API version {value.api_version}; "
            f"Dander requires {PROVIDER_API_VERSION}"
        )
    if value.kind is not kind or value.provider_id != provider_id:
        raise ProviderFactoryError(
            f"Provider {kind.value}.{provider_id} factory identity does not match registration"
        )
    if not callable(value.build):
        raise ProviderFactoryError(
            f"Provider {kind.value}.{provider_id} factory build is not callable"
        )
    return value


def _validate_provider_id(provider_id: str) -> None:
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ProviderFactoryError(f"Invalid provider id {provider_id!r}")
