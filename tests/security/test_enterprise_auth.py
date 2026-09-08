"""OAuth2 JWT and OAuth1 TBA behavior without external requests or real credentials."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dander.ingestion import SourceConfig
from dander.security import EnvironmentSecretStore, OAuth1TBA, OAuth2JWT, OAuthTokenError

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _Secrets:
    values: dict[str, str]
    accesses: list[str] = field(default_factory=list)

    def get_secret(self, reference: str) -> str:
        self.accesses.append(reference)
        return self.values[reference]


@dataclass
class _JwtServer:
    payload: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        url: str,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> httpx.Response:
        self.calls.append(
            {
                "url": url,
                "data": dict(data),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return httpx.Response(
            200,
            json=self.payload,
            request=httpx.Request("POST", url),
        )


def test_oauth2_jwt_signs_exchanges_and_caches_without_exposing_key() -> None:
    secrets = _Secrets({"issuer-ref": "service@example.test", "key-ref": "synthetic-key-material"})
    server = _JwtServer({"access_token": "synthetic-access-token", "expires_in": 3600})
    signing_calls: list[dict[str, Any]] = []

    def sign(
        *,
        issuer: str,
        private_key: str,
        audience: str,
        scope: str | None,
        subject: str | None,
        issued_at: int,
        assertion_lifetime: int,
    ) -> str:
        signing_calls.append(
            {
                "issuer": issuer,
                "private_key": private_key,
                "audience": audience,
                "scope": scope,
                "subject": subject,
                "issued_at": issued_at,
                "assertion_lifetime": assertion_lifetime,
            }
        )
        return "synthetic-signed-assertion"

    strategy = OAuth2JWT(
        secrets,
        issuer_ref="issuer-ref",
        private_key_ref="key-ref",
        token_url="https://auth.example.test/token",
        scope="records.read",
        subject="delegated@example.test",
        request_token=server,
        sign_assertion=sign,
        clock=lambda: 100.0,
        wall_clock=lambda: 1_800_000_000.0,
    )

    first = strategy.apply(httpx.Request("GET", "https://api.example.test/records"))
    second = strategy.apply(httpx.Request("GET", "https://api.example.test/records"))

    assert first.headers["Authorization"] == "Bearer synthetic-access-token"
    assert second.headers["Authorization"] == "Bearer synthetic-access-token"
    assert secrets.accesses == ["issuer-ref", "key-ref"]
    assert signing_calls == [
        {
            "issuer": "service@example.test",
            "private_key": "synthetic-key-material",
            "audience": "https://auth.example.test/token",
            "scope": "records.read",
            "subject": "delegated@example.test",
            "issued_at": 1_800_000_000,
            "assertion_lifetime": 3600,
        }
    ]
    assert server.calls[0]["data"] == {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": "synthetic-signed-assertion",
    }


def test_oauth2_jwt_refresh_and_errors_are_credential_free() -> None:
    private_key = "do-not-echo-private-key"
    secrets = _Secrets({"issuer": "service@example.test", "key": private_key})

    def sign(
        *,
        issuer: str,
        private_key: str,
        audience: str,
        scope: str | None,
        subject: str | None,
        issued_at: int,
        assertion_lifetime: int,
    ) -> str:
        del issuer, private_key, audience, scope, subject, issued_at, assertion_lifetime
        return "assertion"

    strategy = OAuth2JWT(
        secrets,
        issuer_ref="issuer",
        private_key_ref="key",
        token_url="https://auth.example.test/token",
        scope="records.read",
        request_token=_JwtServer({}),
        sign_assertion=sign,
    )

    with pytest.raises(OAuthTokenError) as exc_info:
        strategy.apply(httpx.Request("GET", "https://api.example.test"))

    assert private_key not in str(exc_info.value)
    assert "assertion" not in str(exc_info.value)


def test_oauth2_jwt_accepts_provider_response_without_expiry() -> None:
    secrets = _Secrets({"issuer": "service@example.test", "key": "synthetic-key"})

    def sign(
        *,
        issuer: str,
        private_key: str,
        audience: str,
        scope: str | None,
        subject: str | None,
        issued_at: int,
        assertion_lifetime: int,
    ) -> str:
        del issuer, private_key, audience, scope, subject, issued_at, assertion_lifetime
        return "assertion"

    strategy = OAuth2JWT(
        secrets,
        issuer_ref="issuer",
        private_key_ref="key",
        token_url="https://auth.example.test/token",
        subject="salesforce-user@example.test",
        request_token=_JwtServer({"access_token": "short-cache-token"}),
        sign_assertion=sign,
        default_expires_in=300,
    )

    request = strategy.apply(httpx.Request("GET", "https://api.example.test"))

    assert request.headers["Authorization"] == "Bearer short-cache-token"


def test_oauth2_jwt_default_signer_emits_expected_claims() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    server = _JwtServer({"access_token": "signed-token", "expires_in": 300})
    strategy = OAuth2JWT(
        _Secrets({"issuer": "service@example.test", "key": private_key}),
        issuer_ref="issuer",
        private_key_ref="key",
        token_url="https://auth.example.test/token",
        audience="https://auth.example.test",
        scope="records.read",
        assertion_lifetime=120,
        request_token=server,
        wall_clock=lambda: 1_800_000_000.0,
    )

    strategy.apply(httpx.Request("GET", "https://api.example.test"))

    assertion = server.calls[0]["data"]["assertion"]
    assert jwt.get_unverified_header(assertion) == {"alg": "RS256", "typ": "JWT"}
    claims = jwt.decode(
        assertion,
        key.public_key(),
        algorithms=["RS256"],
        audience="https://auth.example.test",
        issuer="service@example.test",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {
        "aud": "https://auth.example.test",
        "exp": 1_800_000_120,
        "iat": 1_800_000_000,
        "iss": "service@example.test",
        "scope": "records.read",
    }


def test_oauth2_jwt_signing_and_expiry_without_google_sdks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import importlib.abc
import sys

forbidden = ("google.auth", "google.api_core", "google.cloud", "dlt")

class BlockUnselectedSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in forbidden):
            raise ImportError("Generic JWT signing attempted provider import: " + fullname)

sys.meta_path.insert(0, BlockUnselectedSDKs())
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dander.security.oauth_jwt import _sign_rs256

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_key = key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode("ascii")
assertion = _sign_rs256(
    issuer="service@example.test", private_key=private_key,
    audience="https://auth.example.test", scope=None,
    subject="delegated@example.test", issued_at=1_600_000_000, assertion_lifetime=120,
)
claims = jwt.decode(
    assertion, key.public_key(), algorithms=["RS256"],
    audience="https://auth.example.test", issuer="service@example.test",
    options={"verify_exp": False},
)
assert claims["sub"] == "delegated@example.test"
assert "scope" not in claims
assert claims["exp"] == claims["iat"] + 120
try:
    jwt.decode(assertion, key.public_key(), algorithms=["RS256"],
               audience="https://auth.example.test")
except jwt.ExpiredSignatureError:
    pass
else:
    raise AssertionError("Expired assertion was accepted")
assert not any(
    name == prefix or name.startswith(prefix + ".")
    for name in sys.modules for prefix in forbidden
)
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_oauth2_jwt_invalid_private_key_keeps_public_error() -> None:
    server = _JwtServer({"access_token": "unused"})
    strategy = OAuth2JWT(
        _Secrets({"issuer": "service@example.test", "key": "invalid-private-key"}),
        issuer_ref="issuer",
        private_key_ref="key",
        token_url="https://auth.example.test/token",
        request_token=server,
    )

    with pytest.raises(OAuthTokenError, match="^OAuth JWT token exchange failed$"):
        strategy.apply(httpx.Request("GET", "https://api.example.test"))

    assert server.calls == []


def test_oauth1_tba_builds_deterministic_rfc5849_header() -> None:
    secrets = _Secrets(
        {
            "consumer-key-ref": "consumer key",
            "consumer-secret-ref": "consumer/secret",
            "token-id-ref": "token id",
            "token-secret-ref": "token&secret",
        }
    )
    strategy = OAuth1TBA(
        secrets,
        account_id="123456_SB1",
        consumer_key_ref="consumer-key-ref",
        consumer_secret_ref="consumer-secret-ref",
        token_id_ref="token-id-ref",
        token_secret_ref="token-secret-ref",
        nonce=lambda: "fixed-nonce",
        clock=lambda: 1_800_000_000.0,
    )

    request = strategy.apply(
        httpx.Request(
            "GET",
            "https://123456.suitetalk.api.netsuite.com/services/rest/record/v1/customer"
            "?limit=100&query=a%20b",
        )
    )

    assert request.headers["Authorization"] == (
        'OAuth realm="123456_SB1", oauth_consumer_key="consumer%20key", '
        'oauth_nonce="fixed-nonce", oauth_signature_method="HMAC-SHA256", '
        'oauth_timestamp="1800000000", oauth_token="token%20id", oauth_version="1.0", '
        'oauth_signature="XHx0%2FuDMVv5vijPgY5n%2Betva7QFLNTzuM6DMCjyO0ZA%3D"'
    )
    assert secrets.accesses == [
        "consumer-key-ref",
        "consumer-secret-ref",
        "token-id-ref",
        "token-secret-ref",
    ]


@pytest.mark.parametrize(
    "config",
    [
        {
            "name": "salesforce",
            "base_url": "https://example.test",
            "auth_strategy": "oauth2_jwt",
            "auth_refs": {"issuer": "issuer-ref", "private_key": "key-ref"},
            "auth_options": {
                "token_url": "https://auth.example.test/token",
                "audience": "https://auth.example.test",
                "assertion_lifetime": 120,
                "scope": "records.read",
            },
        },
        {
            "name": "netsuite",
            "base_url": "https://example.test",
            "auth_strategy": "oauth1_tba",
            "auth_refs": {
                "consumer_key": "consumer-key-ref",
                "consumer_secret": "consumer-secret-ref",
                "token_id": "token-id-ref",
                "token_secret": "token-secret-ref",
            },
            "auth_options": {"account_id": "123456_SB1"},
        },
    ],
)
def test_source_config_accepts_complete_enterprise_auth(config: dict[str, Any]) -> None:
    assert SourceConfig.model_validate(config).auth_strategy == config["auth_strategy"]


@pytest.mark.parametrize(
    "config",
    [
        {
            "name": "salesforce",
            "base_url": "https://example.test",
            "auth_strategy": "oauth2_jwt",
            "auth_refs": {"issuer": "issuer-ref"},
            "auth_options": {
                "token_url": "https://auth.example.test/token",
                "scope": "records.read",
            },
        },
        {
            "name": "netsuite",
            "base_url": "https://example.test",
            "auth_strategy": "oauth1_tba",
            "auth_refs": {"consumer_key": "consumer-key-ref"},
            "auth_options": {"account_id": "123456_SB1"},
        },
    ],
)
def test_source_config_rejects_missing_enterprise_auth_refs(config: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="requires auth_refs"):
        SourceConfig.model_validate(config)


def test_cli_auth_factory_dispatches_enterprise_strategies() -> None:
    from dander.cli.main import _build_auth

    store = EnvironmentSecretStore({})
    jwt_config = SourceConfig.model_validate(
        {
            "name": "salesforce",
            "base_url": "https://example.test",
            "auth_strategy": "oauth2_jwt",
            "auth_refs": {"issuer": "issuer-ref", "private_key": "key-ref"},
            "auth_options": {
                "token_url": "https://auth.example.test/token",
                "audience": "https://auth.example.test",
                "assertion_lifetime": 120,
                "subject": "user@example.test",
            },
        }
    )
    tba_config = SourceConfig.model_validate(
        {
            "name": "netsuite",
            "base_url": "https://example.test",
            "auth_strategy": "oauth1_tba",
            "auth_refs": {
                "consumer_key": "consumer-key-ref",
                "consumer_secret": "consumer-secret-ref",
                "token_id": "token-id-ref",
                "token_secret": "token-secret-ref",
            },
            "auth_options": {"account_id": "123456_SB1"},
        }
    )

    jwt_auth = _build_auth(jwt_config, store)
    assert isinstance(jwt_auth, OAuth2JWT)
    assert jwt_auth._audience == "https://auth.example.test"
    assert jwt_auth._assertion_lifetime == 120
    assert isinstance(_build_auth(tba_config, store), OAuth1TBA)


@pytest.mark.parametrize(
    "auth_options",
    [
        {"token_url": "https://auth.example.test/token", "audience": "http://invalid.test"},
        {"token_url": "https://auth.example.test/token", "assertion_lifetime": 59},
    ],
)
def test_source_config_rejects_invalid_jwt_assertion_options(
    auth_options: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        SourceConfig.model_validate(
            {
                "name": "salesforce",
                "base_url": "https://example.test",
                "auth_strategy": "oauth2_jwt",
                "auth_refs": {"issuer": "issuer-ref", "private_key": "key-ref"},
                "auth_options": auth_options,
            }
        )
