"""Resolve launcher-projected secret references only for one runtime execution."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.security import SecretRuntime

if TYPE_CHECKING:
    from collections.abc import Iterator, MutableMapping

    from dander.core.interfaces import SecretStoreProvider

_BINDINGS_ENV = "DANDER_SECRET_BINDINGS_JSON"
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,126}$")
_SUPPORTED_PROVIDERS = frozenset({"oci_vault"})


class RuntimeSecretBindingError(RuntimeError):
    """A launcher supplied malformed, ambiguous, or unresolvable secret bindings."""


@contextmanager
def projected_secret_environment(
    *,
    environ: MutableMapping[str, str] = os.environ,
) -> Iterator[None]:
    """Temporarily resolve validated launcher bindings, then remove every secret value."""
    raw = environ.get(_BINDINGS_ENV)
    if raw is None:
        yield
        return
    bindings = _parse_bindings(raw)
    overlap = sorted(set(bindings) & set(environ))
    if overlap:
        raise RuntimeSecretBindingError(
            "Launcher secret bindings overlap preconfigured environment values: "
            + ", ".join(overlap)
        )
    resolved: dict[str, str] = {}
    try:
        registry = default_provider_registry()
        stores: dict[str, SecretStoreProvider] = {}
        for name, (provider, reference) in bindings.items():
            store = stores.get(provider)
            if store is None:
                config = registry.parse(ProviderKind.SECRETS, {"provider": provider})
                runtime = registry.build(ProviderKind.SECRETS, config)
                if not isinstance(runtime, SecretRuntime):
                    raise RuntimeSecretBindingError(
                        "Selected launcher secret provider returned an invalid runtime"
                    )
                store = runtime.store
                stores[provider] = store
            value = store.get_secret(reference)
            if not isinstance(value, str) or not value:
                raise RuntimeSecretBindingError(
                    f"Launcher secret binding {name} resolved to an empty value"
                )
            resolved[name] = value
        environ.update(resolved)
        yield
    except ProviderFactoryError as error:
        raise RuntimeSecretBindingError(str(error)) from error
    finally:
        for name in resolved:
            environ.pop(name, None)


def _parse_bindings(raw: str) -> dict[str, tuple[str, str]]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeSecretBindingError("Launcher secret bindings are not valid JSON") from error
    if not isinstance(document, dict) or not document:
        raise RuntimeSecretBindingError("Launcher secret bindings must be a non-empty object")
    parsed: dict[str, tuple[str, str]] = {}
    for name, binding in document.items():
        if not isinstance(name, str) or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise RuntimeSecretBindingError("Launcher secret binding names are invalid")
        if not isinstance(binding, dict) or set(binding) != {"provider", "reference"}:
            raise RuntimeSecretBindingError("Launcher secret binding entries are invalid")
        provider = binding.get("provider")
        reference = binding.get("reference")
        if provider not in _SUPPORTED_PROVIDERS or not isinstance(reference, str):
            raise RuntimeSecretBindingError("Launcher secret binding provider is unsupported")
        expected_prefix = "oci-vault://"
        if not reference.startswith(expected_prefix) or len(reference) > 1_024:
            raise RuntimeSecretBindingError("Launcher secret binding reference is invalid")
        parsed[name] = (provider, reference)
    return parsed


__all__ = ["RuntimeSecretBindingError", "projected_secret_environment"]
