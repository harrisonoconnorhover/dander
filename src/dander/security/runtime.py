"""Provider-neutral secret-resolution runtime composition."""

from __future__ import annotations

from dataclasses import dataclass

from dander.core.interfaces import SecretStoreProvider


@dataclass(frozen=True, slots=True)
class SecretCapabilities:
    """Resolution behavior exposed by one secret provider."""

    provider_id: str
    reference_forms: frozenset[str]
    environment_indirection: bool
    audited_access: bool

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("secret capabilities require a provider id")
        if not self.reference_forms:
            raise ValueError("secret capabilities require at least one reference form")


@dataclass(frozen=True, slots=True)
class SecretRuntime:
    """A selected secret store and its declared behavior."""

    provider_id: str
    store: SecretStoreProvider
    capabilities: SecretCapabilities

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("secret runtime requires a provider id")
        if self.capabilities.provider_id != self.provider_id:
            raise ValueError("secret runtime and capabilities provider ids must match")
        if not isinstance(self.store, SecretStoreProvider):
            raise TypeError("secret runtime store must implement SecretStoreProvider")
