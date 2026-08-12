"""Run-scoped launcher secret-reference resolution."""

from __future__ import annotations

import json

import pytest

from dander.runtime_secrets import (
    RuntimeSecretBindingError,
    projected_secret_environment,
)


class _Store:
    def __init__(self) -> None:
        self.references: list[str] = []

    def get_secret(self, reference: str) -> str:
        self.references.append(reference)
        return "resolved-value"


class _Runtime:
    def __init__(self, store: _Store) -> None:
        self.store = store


class _Registry:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def parse(self, kind: object, raw: object) -> object:
        del kind
        assert raw == {"provider": "oci_vault"}
        return object()

    def build(self, kind: object, config: object) -> object:
        del kind, config
        from dander.security import SecretCapabilities, SecretRuntime

        return SecretRuntime(
            provider_id="oci_vault",
            store=self.store,
            capabilities=SecretCapabilities(
                provider_id="oci_vault",
                reference_forms=frozenset({"oci_vault_secret_name"}),
                environment_indirection=False,
                audited_access=True,
            ),
        )


def test_projected_secret_environment_is_scoped_and_resolves_only_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store()
    monkeypatch.setattr(
        "dander.runtime_secrets.default_provider_registry", lambda: _Registry(store)
    )
    environment = {
        "DANDER_SECRET_BINDINGS_JSON": json.dumps(
            {
                "DANDER_POSTGRES_DSN": {
                    "provider": "oci_vault",
                    "reference": "oci-vault://ocid1.vault.oc1.iad.unit/secrets/postgres-dsn",
                }
            }
        )
    }

    with projected_secret_environment(environ=environment):
        assert environment["DANDER_POSTGRES_DSN"] == "resolved-value"

    assert "DANDER_POSTGRES_DSN" not in environment
    assert store.references == ["oci-vault://ocid1.vault.oc1.iad.unit/secrets/postgres-dsn"]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({}, "non-empty"),
        ({"bad-name": {}}, "names"),
        (
            {"TOKEN": {"provider": "environment", "reference": "env://TOKEN"}},
            "provider",
        ),
        (
            {"TOKEN": {"provider": "oci_vault", "reference": "plain-text"}},
            "reference",
        ),
    ],
)
def test_projected_secret_environment_fails_closed(
    document: object,
    message: str,
) -> None:
    environment = {"DANDER_SECRET_BINDINGS_JSON": json.dumps(document)}
    with (
        pytest.raises(RuntimeSecretBindingError, match=message),
        projected_secret_environment(environ=environment),
    ):
        pass


def test_projected_secret_environment_rejects_ambiguous_existing_values() -> None:
    environment = {
        "DANDER_SECRET_BINDINGS_JSON": json.dumps(
            {
                "TOKEN": {
                    "provider": "oci_vault",
                    "reference": "oci-vault://ocid1.vault.oc1.iad.unit/secrets/token",
                }
            }
        ),
        "TOKEN": "already-set",
    }
    with (
        pytest.raises(RuntimeSecretBindingError, match="preconfigured"),
        projected_secret_environment(environ=environment),
    ):
        pass
