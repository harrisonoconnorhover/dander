"""Hosted OIDC projections, access-token validation, and authorization contracts."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import JsonValue, ValidationError

from dander.control import InMemoryGraphStore
from dander.control.application import ControlApplication
from dander.control.auth import (
    BoundedJWKSResolver,
    ControlAuthError,
    ControlCapability,
    HostedOIDCDeploymentInput,
    JWKSResolutionError,
    capabilities_for_roles,
    project_hosted_oidc,
    verify_oidc_projection_alignment,
)
from dander.control.http import create_control_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from httpx import Response

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-signing-key"
_GRAPH = {"name": "hosted_graph", "nodes": [], "edges": []}


def _jwk(private_key: RSAPrivateKey = _PRIVATE_KEY, *, kid: str = _KID) -> dict[str, Any]:
    value = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    assert isinstance(value, dict)
    return {**value, "kid": kid, "use": "sig", "alg": "RS256"}


def _jwks(*keys: dict[str, Any]) -> bytes:
    return json.dumps({"keys": list(keys or (_jwk(),))}).encode()


def _config(**updates: object) -> HostedOIDCDeploymentInput:
    values: dict[str, object] = {
        "api_url": "https://control.example.test",
        "issuer": "https://identity.example.test",
        "jwks_uri": "https://identity.example.test/.well-known/jwks.json",
        "public_client_id": "druff-public-client",
        "api_audience": "https://control.example.test/api",
        "redirect_uri": "https://druff.example.test/auth/callback",
        "logout_uri": "https://druff.example.test/signed-out",
        "allowed_origins": ("https://druff.example.test",),
    }
    values.update(updates)
    return HostedOIDCDeploymentInput.model_validate(values)


def _token(
    roles: list[str] | str | None = None,
    *,
    config: HostedOIDCDeploymentInput | None = None,
    private_key: RSAPrivateKey = _PRIVATE_KEY,
    kid: str = _KID,
    claims: dict[str, JsonValue] | None = None,
) -> str:
    selected = config or _config()
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": selected.issuer,
        "aud": selected.api_audience,
        "sub": "person-123",
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
        selected.role_claim: roles if roles is not None else ["viewer"],
    }
    payload.update(claims or {})
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid, "typ": "at+jwt"},
    )


def _authorization(
    role: str,
    *,
    config: HostedOIDCDeploymentInput | None = None,
    email: str | None = None,
    email_verified: bool | None = None,
    groups: list[str] | None = None,
) -> dict[str, str]:
    claims: dict[str, JsonValue] = {}
    if email is not None:
        claims["email"] = email
    if email_verified is not None:
        claims["email_verified"] = email_verified
    if groups is not None:
        claims["groups"] = cast("JsonValue", groups)
    return {"Authorization": f"Bearer {_token([role], config=config, claims=claims)}"}


def _client(
    *,
    config: HostedOIDCDeploymentInput | None = None,
    fetcher: Callable[[], bytes] = _jwks,
) -> TestClient:
    selected = config or _config()
    application = ControlApplication(InMemoryGraphStore(), projects=("demo-project",))
    return TestClient(create_control_app(application, oidc=selected, oidc_jwks_fetcher=fetcher))


def _create(client: TestClient, role: str = "editor") -> Response:
    return cast(
        "Response",
        client.post(
            "/v1/projects/demo-project/graphs",
            json={"graph": "alpha-graph", "document": _GRAPH},
            headers={**_authorization(role), "Idempotency-Key": f"create-{role}-0001"},
        ),
    )


def test_one_input_projects_exact_secret_free_server_client_and_bootstrap_contracts() -> None:
    config = _config()
    projected = project_hosted_oidc(config)

    assert projected.server.issuer == projected.bootstrap.issuer == config.issuer
    assert projected.server.api_audience == projected.bootstrap.api_audience
    assert projected.public_client.client_id == projected.bootstrap.public_client_id
    assert projected.public_client.grant_types == ("authorization_code",)
    assert projected.public_client.response_types == ("code",)
    assert projected.public_client.token_endpoint_auth_method == "none"
    assert projected.public_client.code_challenge_methods == ("S256",)
    assert projected.bootstrap.contract.id == "io.dander.control.contracts/v1"
    assert projected.bootstrap.contract.sha256 != "0" * 64
    serialized = projected.model_dump_json()
    assert "secret" not in serialized.casefold()
    assert "refresh_token" not in serialized
    assert "provider_credentials" not in serialized

    wrong_bootstrap = projected.bootstrap.model_copy(update={"api_audience": "wrong"})
    divergent = projected.model_copy(update={"bootstrap": wrong_bootstrap})
    with pytest.raises(ValueError, match="do not agree"):
        verify_oidc_projection_alignment(config, divergent)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"api_audience": "druff-public-client"},
            "client ID and Control API audience must differ",
        ),
        ({"issuer": "http://identity.example.test"}, "absolute HTTPS"),
        ({"allowed_origins": ("*",)}, "exact HTTPS origins"),
        (
            {"redirect_uri": "https://elsewhere.example.test/auth/callback"},
            "explicitly allowed",
        ),
        ({"allowed_algorithms": ("HS256",)}, "asymmetric algorithms"),
    ],
)
def test_deployment_input_rejects_ambiguous_or_unsafe_topologies(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _config(**updates)


def test_hosted_mode_fails_closed_and_rejects_an_id_token_audience() -> None:
    config = _config()
    with _client(config=config) as client:
        missing = client.get("/v1/projects")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "authentication_required"
        assert missing.headers["www-authenticate"] == 'Bearer realm="dander-control"'

        id_token = _token(["viewer"], config=config, claims={"aud": config.public_client_id})
        wrong_audience = client.get("/v1/projects", headers={"Authorization": f"Bearer {id_token}"})
        assert wrong_audience.status_code == 401
        assert wrong_audience.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "token",
    [
        _token(["viewer"], claims={"iss": "https://other-issuer.example.test"}),
        _token(["viewer"], claims={"exp": int(time.time()) - 300}),
        _token(["viewer"], private_key=_OTHER_PRIVATE_KEY),
        _token("viewer"),
    ],
)
def test_invalid_signature_issuer_expiry_or_claim_shape_is_rejected(token: str) -> None:
    with _client() as client:
        response = client.get("/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert token not in response.text


def test_optional_subject_email_and_group_allowlists_fail_closed() -> None:
    config = _config(
        subject_allowlist=("person-123",),
        email_allowlist=("person@example.test",),
        group_allowlist=("druff-users",),
    )
    with _client(config=config) as client:
        unverified = client.get(
            "/v1/projects",
            headers=_authorization(
                "viewer",
                config=config,
                email="person@example.test",
                email_verified=False,
                groups=["druff-users"],
            ),
        )
        accepted = client.get(
            "/v1/projects",
            headers=_authorization(
                "viewer",
                config=config,
                email="person@example.test",
                email_verified=True,
                groups=["druff-users"],
            ),
        )
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "identity_not_allowed"
    assert accepted.status_code == 200


def test_roles_map_centrally_to_all_five_capabilities() -> None:
    assert capabilities_for_roles(("viewer",)) == {ControlCapability.READ}
    assert capabilities_for_roles(("editor",)) == {
        ControlCapability.READ,
        ControlCapability.EDIT,
        ControlCapability.VALIDATE_PREVIEW,
    }
    assert capabilities_for_roles(("operator",)) == {
        ControlCapability.READ,
        ControlCapability.EDIT,
        ControlCapability.VALIDATE_PREVIEW,
        ControlCapability.RUN,
    }
    assert capabilities_for_roles(("admin",)) == set(ControlCapability)
    with pytest.raises(ControlAuthError):
        capabilities_for_roles(("owner",))


def test_each_route_capability_is_enforced_server_side() -> None:
    with _client() as client:
        assert client.get("/v1/projects", headers=_authorization("viewer")).status_code == 200
        assert _create(client, "viewer").status_code == 403

        created = _create(client, "editor")
        assert created.status_code == 201
        assert (
            client.post(
                "/v1/projects/demo-project/graphs/alpha-graph/validate",
                headers={**_authorization("editor"), "If-Match": created.headers["etag"]},
            ).status_code
            == 200
        )
        assert (
            client.delete(
                "/v1/projects/demo-project/graphs/alpha-graph",
                headers={
                    **_authorization("editor"),
                    "If-Match": created.headers["etag"],
                    "Idempotency-Key": "delete-editor-0001",
                },
            ).status_code
            == 403
        )
        run = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/runs",
            headers={
                **_authorization("operator"),
                "If-Match": created.headers["etag"],
                "Idempotency-Key": "start-operator-0001",
            },
        )
        assert run.status_code == 501
        assert run.json()["error"]["code"] == "operation_unavailable"
        deleted = client.delete(
            "/v1/projects/demo-project/graphs/alpha-graph",
            headers={
                **_authorization("admin"),
                "If-Match": created.headers["etag"],
                "Idempotency-Key": "delete-admin-0001",
            },
        )
        assert deleted.status_code == 204


def test_capabilities_are_filtered_for_the_authenticated_role() -> None:
    with _client() as client:
        viewer = client.get("/v1/capabilities", headers=_authorization("viewer")).json()
        editor = client.get("/v1/capabilities", headers=_authorization("editor")).json()
        admin = client.get("/v1/capabilities", headers=_authorization("admin")).json()
    assert viewer["operations"] == ["graph.read"]
    assert set(editor["operations"]) == {"graph.read", "graph.edit", "graph.validate"}
    assert set(admin["operations"]) == {
        "graph.read",
        "graph.edit",
        "graph.delete",
        "graph.validate",
    }


def test_exact_cors_preflight_errors_etags_and_security_headers() -> None:
    origin = "https://druff.example.test"
    with _client() as client:
        preflight = client.options(
            "/v1/projects/demo-project/graphs",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "Authorization, Content-Type, Idempotency-Key, If-Match"
                ),
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        assert "authorization" in preflight.headers["access-control-allow-headers"].casefold()
        assert preflight.headers.get("access-control-allow-credentials") is None

        denied = _create_with_origin(client, "viewer", origin)
        assert denied.status_code == 403
        assert denied.headers["access-control-allow-origin"] == origin
        assert denied.headers["content-security-policy"].startswith("default-src 'none'")

        created = _create_with_origin(client, "editor", origin)
        assert created.status_code == 201
        assert created.headers["access-control-allow-origin"] == origin
        exposed = created.headers["access-control-expose-headers"].casefold()
        assert "etag" in exposed and "x-correlation-id" in exposed

        wrong_origin = client.get(
            "/v1/projects",
            headers={**_authorization("viewer"), "Origin": "https://evil.example.test"},
        )
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["error"]["code"] == "origin_not_allowed"
        assert "access-control-allow-origin" not in wrong_origin.headers


def _create_with_origin(client: TestClient, role: str, origin: str) -> Response:
    return cast(
        "Response",
        client.post(
            "/v1/projects/demo-project/graphs",
            json={"graph": f"{role}-graph", "document": _GRAPH},
            headers={
                **_authorization(role),
                "Origin": origin,
                "Idempotency-Key": f"create-{role}-origin-0001",
            },
        ),
    )


def test_url_tokens_are_rejected_before_routing_and_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "must-not-appear-in-logs"
    with _client() as client, caplog.at_level(logging.INFO, logger="dander.control.audit"):
        response = client.get(f"/v1/projects?access_token={secret}")
        openapi = client.get("/openapi.json", headers=_authorization("viewer"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_in_url"
    assert secret not in caplog.text
    assert openapi.status_code == 404


def test_jwks_unknown_kid_refresh_is_single_flight_and_has_a_shared_cooldown() -> None:
    calls = 0
    calls_lock = threading.Lock()

    def fetch() -> bytes:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return _jwks(_jwk())

    resolver = BoundedJWKSResolver(_config(), fetcher=fetch, monotonic=lambda: 100.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(resolver.resolve, "unknown-key", "RS256") for _ in range(8)]
    assert all(isinstance(future.exception(), JWKSResolutionError) for future in futures)
    assert calls == 1
    with pytest.raises(JWKSResolutionError):
        resolver.resolve("another-unknown-key", "RS256")
    assert calls == 1


def test_jwks_failed_refresh_retains_the_last_good_key() -> None:
    now = [0.0]
    responses: list[bytes | Exception] = [_jwks(_jwk()), RuntimeError("offline")]

    def fetch() -> bytes:
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    resolver = BoundedJWKSResolver(
        _config(jwks_cache_seconds=30.0),
        fetcher=fetch,
        monotonic=lambda: now[0],
    )
    first = resolver.resolve(_KID, "RS256")
    now[0] = 31.0
    retained = resolver.resolve(_KID, "RS256")
    assert retained.key.public_numbers() == first.key.public_numbers()
    assert responses == []


@pytest.mark.parametrize(
    ("config", "payload"),
    [
        (_config(jwks_max_response_bytes=1024), b"{" + b"x" * 1024),
        (_config(jwks_max_keys=1), _jwks(_jwk(), _jwk(kid="second-key"))),
    ],
)
def test_jwks_response_size_and_key_count_are_bounded(
    config: HostedOIDCDeploymentInput, payload: bytes
) -> None:
    resolver = BoundedJWKSResolver(config, fetcher=lambda: payload)
    with pytest.raises(JWKSResolutionError):
        resolver.resolve(_KID, "RS256")


def test_sync_jwks_fetch_never_runs_on_the_asgi_event_loop() -> None:
    def fetch() -> bytes:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return _jwks(_jwk())

    with _client(fetcher=fetch) as client:
        response = client.get("/v1/projects", headers=_authorization("viewer"))
    assert response.status_code == 200
