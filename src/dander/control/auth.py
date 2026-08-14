"""Provider-neutral hosted OIDC trust, projection, and authorization boundary."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import Request  # noqa: TC002 - FastAPI resolves dependency annotations at runtime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dander.control.models import (
    CompatibilityRange,
    ContractIdentity,
    ControlBootstrapDescriptor,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_CLAIM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=-]{0,255}$")
_ASYMMETRIC_ALGORITHMS: Final = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
)
_MAX_BEARER_TOKEN_BYTES: Final = 16 * 1024
_MAX_ROLES: Final = 16
_MAX_GROUPS: Final = 128


class HostedOIDCDeploymentInput(BaseModel):
    """One immutable, non-secret input for every hosted OIDC projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_url: str = Field(min_length=1, max_length=2048)
    issuer: str = Field(min_length=1, max_length=2048)
    jwks_uri: str = Field(min_length=1, max_length=2048)
    public_client_id: str = Field(min_length=1, max_length=256)
    api_audience: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    logout_uri: str = Field(min_length=1, max_length=2048)
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=16)
    role_claim: str = "roles"
    email_claim: str = "email"
    group_claim: str = "groups"
    subject_allowlist: tuple[str, ...] = Field(default=(), max_length=256)
    email_allowlist: tuple[str, ...] = Field(default=(), max_length=256)
    group_allowlist: tuple[str, ...] = Field(default=(), max_length=256)
    allowed_algorithms: tuple[str, ...] = Field(default=("RS256",), min_length=1, max_length=4)
    jwks_timeout_seconds: float = Field(default=5.0, ge=0.1, le=10.0)
    jwks_max_response_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)
    jwks_max_keys: int = Field(default=32, ge=1, le=64)
    jwks_cache_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)
    jwks_refresh_cooldown_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    clock_skew_seconds: int = Field(default=30, ge=0, le=120)

    @field_validator("api_url", "issuer", "jwks_uri", "redirect_uri", "logout_uri")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        _require_https_url(value)
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Hosted OIDC allowed origins must be unique.")
        for value in values:
            if value == "*" or _origin(value) != value:
                raise ValueError("Hosted OIDC origins must be exact HTTPS origins.")
        return values

    @field_validator("role_claim", "email_claim", "group_claim")
    @classmethod
    def validate_claim_name(cls, value: str) -> str:
        if _CLAIM_NAME.fullmatch(value) is None:
            raise ValueError("OIDC claim names must be explicit bounded identifiers.")
        return value

    @field_validator("subject_allowlist", "email_allowlist", "group_allowlist")
    @classmethod
    def validate_allowlist(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("OIDC allowlist entries must be unique.")
        if any(not value or len(value) > 512 for value in values):
            raise ValueError("OIDC allowlist entries must contain 1 to 512 characters.")
        return values

    @field_validator("allowed_algorithms")
    @classmethod
    def validate_algorithms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or not set(values).issubset(_ASYMMETRIC_ALGORITHMS):
            raise ValueError("Hosted OIDC accepts only unique approved asymmetric algorithms.")
        return values

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        if self.public_client_id == self.api_audience:
            raise ValueError("The public SPA client ID and Control API audience must differ.")
        origins = set(self.allowed_origins)
        if _origin(self.redirect_uri) not in origins or _origin(self.logout_uri) not in origins:
            raise ValueError("Redirect and logout origins must both be explicitly allowed.")
        return self


class OIDCServerTrustProjection(BaseModel):
    """Exact private trust settings consumed by the hosted Control API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str
    jwks_uri: str
    api_audience: str
    allowed_origins: tuple[str, ...]
    role_claim: str
    email_claim: str
    group_claim: str
    allowed_algorithms: tuple[str, ...]


class PublicOIDCClientProjection(BaseModel):
    """Secret-free public client registration projected for an external issuer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    application_type: Literal["spa"] = "spa"
    client_id: str
    redirect_uris: tuple[str, ...]
    post_logout_redirect_uris: tuple[str, ...]
    grant_types: tuple[Literal["authorization_code"], ...] = ("authorization_code",)
    response_types: tuple[Literal["code"], ...] = ("code",)
    token_endpoint_auth_method: Literal["none"] = "none"
    code_challenge_methods: tuple[Literal["S256"], ...] = ("S256",)


class HostedOIDCProjections(BaseModel):
    """All generated views of one deployment input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server: OIDCServerTrustProjection
    public_client: PublicOIDCClientProjection
    bootstrap: ControlBootstrapDescriptor


def project_hosted_oidc(value: HostedOIDCDeploymentInput) -> HostedOIDCProjections:
    """Project server trust, public registration, and bootstrap from one input."""
    from dander.control.bundle import BUNDLE_ID, packaged_bundle_digest

    projections = HostedOIDCProjections(
        server=OIDCServerTrustProjection(
            issuer=value.issuer,
            jwks_uri=value.jwks_uri,
            api_audience=value.api_audience,
            allowed_origins=value.allowed_origins,
            role_claim=value.role_claim,
            email_claim=value.email_claim,
            group_claim=value.group_claim,
            allowed_algorithms=value.allowed_algorithms,
        ),
        public_client=PublicOIDCClientProjection(
            client_id=value.public_client_id,
            redirect_uris=(value.redirect_uri,),
            post_logout_redirect_uris=(value.logout_uri,),
        ),
        bootstrap=ControlBootstrapDescriptor(
            api_url=value.api_url,
            issuer=value.issuer,
            public_client_id=value.public_client_id,
            api_audience=value.api_audience,
            redirect_uri=value.redirect_uri,
            logout_uri=value.logout_uri,
            contract=ContractIdentity(id=BUNDLE_ID, sha256=packaged_bundle_digest()),
            compatibility=CompatibilityRange(
                minimum_druff_contract="1.0.0",
                maximum_druff_contract="1.x",
            ),
        ),
    )
    verify_oidc_projection_alignment(value, projections)
    return projections


def verify_oidc_projection_alignment(
    source: HostedOIDCDeploymentInput,
    projections: HostedOIDCProjections,
) -> None:
    """Fail if independently serialized deployment views disagree on shared values."""
    expected = {
        "issuer": source.issuer,
        "audience": source.api_audience,
        "client_id": source.public_client_id,
        "redirect": source.redirect_uri,
        "logout": source.logout_uri,
    }
    observed = {
        "issuer": projections.server.issuer,
        "audience": projections.server.api_audience,
        "client_id": projections.public_client.client_id,
        "redirect": projections.public_client.redirect_uris[0],
        "logout": projections.public_client.post_logout_redirect_uris[0],
    }
    bootstrap = projections.bootstrap
    bootstrap_observed = {
        "issuer": bootstrap.issuer,
        "audience": bootstrap.api_audience,
        "client_id": bootstrap.public_client_id,
        "redirect": bootstrap.redirect_uri,
        "logout": bootstrap.logout_uri,
    }
    if expected != observed or expected != bootstrap_observed:
        raise ValueError("Hosted OIDC deployment projections do not agree.")
    if projections.server.allowed_origins != source.allowed_origins:
        raise ValueError("Hosted OIDC CORS projection does not agree with its source.")


class ControlCapability(StrEnum):
    READ = "read"
    EDIT = "edit"
    VALIDATE_PREVIEW = "validate_preview"
    RUN = "run_cancel_replay"
    ADMIN = "delete_admin"


_ROLE_CAPABILITIES: Final[dict[str, frozenset[ControlCapability]]] = {
    "viewer": frozenset({ControlCapability.READ}),
    "editor": frozenset(
        {ControlCapability.READ, ControlCapability.EDIT, ControlCapability.VALIDATE_PREVIEW}
    ),
    "operator": frozenset(
        {
            ControlCapability.READ,
            ControlCapability.EDIT,
            ControlCapability.VALIDATE_PREVIEW,
            ControlCapability.RUN,
        }
    ),
    "admin": frozenset(ControlCapability),
}


def capabilities_for_roles(roles: tuple[str, ...]) -> frozenset[ControlCapability]:
    """Map the four accepted human roles to one centralized capability set."""
    if (
        not roles
        or len(roles) > _MAX_ROLES
        or any(role not in _ROLE_CAPABILITIES for role in roles)
    ):
        raise ControlAuthError(
            HTTPStatus.FORBIDDEN,
            "role_not_allowed",
            "The authenticated identity has no accepted Control role.",
        )
    return frozenset(capability for role in roles for capability in _ROLE_CAPABILITIES[role])


@dataclass(frozen=True, slots=True)
class ControlPrincipal:
    subject: str
    roles: tuple[str, ...]
    capabilities: frozenset[ControlCapability]


class ControlAuthError(RuntimeError):
    """Safe authentication or authorization error for the public HTTP boundary."""

    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class JWKSResolutionError(RuntimeError):
    """A bounded JWKS could not supply the requested signing key."""


class BoundedJWKSResolver:
    """Bounded, single-flight JWKS cache with cooldown and last-good retention."""

    def __init__(
        self,
        config: HostedOIDCDeploymentInput,
        *,
        fetcher: Callable[[], bytes] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._fetcher = fetcher or _http_jwks_fetcher(config)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._last_success = float("-inf")
        self._last_attempt = float("-inf")

    def resolve(self, kid: str, algorithm: str) -> jwt.PyJWK:
        """Resolve one explicitly identified signing key without refresh amplification."""
        if _KEY_ID.fullmatch(kid) is None or algorithm not in self._config.allowed_algorithms:
            raise JWKSResolutionError("The access token signing key is not accepted.")
        now = self._monotonic()
        cached = self._matching_key(kid, algorithm)
        if cached is not None and now - self._last_success <= self._config.jwks_cache_seconds:
            return cached

        with self._lock:
            now = self._monotonic()
            cached = self._matching_key(kid, algorithm)
            if cached is not None and now - self._last_success <= self._config.jwks_cache_seconds:
                return cached
            if now - self._last_attempt < self._config.jwks_refresh_cooldown_seconds:
                if cached is not None:
                    return cached
                raise JWKSResolutionError("The access token signing key is not available.")

            self._last_attempt = now
            try:
                refreshed = self._parse(self._fetcher())
            except Exception as error:
                if cached is not None:
                    return cached
                raise JWKSResolutionError("The signing-key service is unavailable.") from error
            self._keys = refreshed
            self._last_success = now
            resolved = self._matching_key(kid, algorithm)
            if resolved is None:
                raise JWKSResolutionError("The access token signing key is not available.")
            return resolved

    def _matching_key(self, kid: str, algorithm: str) -> jwt.PyJWK | None:
        value = self._keys.get(kid)
        if value is None or value.algorithm_name != algorithm:
            return None
        return value

    def _parse(self, raw: bytes) -> dict[str, jwt.PyJWK]:
        if not raw or len(raw) > self._config.jwks_max_response_bytes:
            raise JWKSResolutionError("The signing-key response exceeded its bound.")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise JWKSResolutionError("The signing-key response is invalid.") from error
        if not isinstance(payload, dict):
            raise JWKSResolutionError("The signing-key response is invalid.")
        items = payload.get("keys")
        if not isinstance(items, list) or not items or len(items) > self._config.jwks_max_keys:
            raise JWKSResolutionError("The signing-key response contains an invalid key set.")
        parsed: dict[str, jwt.PyJWK] = {}
        for item in items:
            if not isinstance(item, dict):
                raise JWKSResolutionError("The signing-key response contains an invalid key.")
            kid = item.get("kid")
            if not isinstance(kid, str) or _KEY_ID.fullmatch(kid) is None or kid in parsed:
                raise JWKSResolutionError("Signing keys require unique bounded key IDs.")
            if item.get("use", "sig") != "sig":
                continue
            key_ops = item.get("key_ops")
            if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
                continue
            declared_algorithm = item.get("alg")
            if (
                declared_algorithm is not None
                and declared_algorithm not in self._config.allowed_algorithms
            ):
                continue
            try:
                key = jwt.PyJWK.from_dict(item)
            except (jwt.PyJWKError, KeyError, TypeError, ValueError) as error:
                raise JWKSResolutionError(
                    "The signing-key response contains an invalid key."
                ) from error
            if key.algorithm_name not in self._config.allowed_algorithms:
                continue
            parsed[kid] = key
        if not parsed:
            raise JWKSResolutionError("The signing-key response contains no accepted signing key.")
        return parsed


class HostedOIDCAuthorizer:
    """Validate one access token and enforce one route capability."""

    def __init__(
        self,
        config: HostedOIDCDeploymentInput,
        *,
        resolver: BoundedJWKSResolver | None = None,
        fetcher: Callable[[], bytes] | None = None,
    ) -> None:
        self.config = config
        self.resolver = resolver or BoundedJWKSResolver(config, fetcher=fetcher)

    def require(self, capability: ControlCapability) -> Callable[[Request], ControlPrincipal]:
        """Build a synchronous FastAPI dependency so JWKS I/O runs off the event loop."""

        def dependency(request: Request) -> ControlPrincipal:
            principal = self.authenticate(request.headers.get("authorization"))
            if capability not in principal.capabilities:
                raise ControlAuthError(
                    HTTPStatus.FORBIDDEN,
                    "capability_required",
                    "The authenticated identity cannot perform this operation.",
                )
            request.state.control_principal = principal
            return principal

        return dependency

    def authenticate(self, authorization: str | None) -> ControlPrincipal:
        token = _bearer_token(authorization)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise _invalid_token() from error
        algorithm = header.get("alg")
        kid = header.get("kid")
        if not isinstance(algorithm, str) or not isinstance(kid, str):
            raise _invalid_token()
        try:
            key = self.resolver.resolve(kid, algorithm)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(self.config.allowed_algorithms),
                audience=self.config.api_audience,
                issuer=self.config.issuer,
                leeway=self.config.clock_skew_seconds,
                options={
                    "require": ["iss", "aud", "exp", "sub", self.config.role_claim],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except (JWKSResolutionError, jwt.PyJWTError, TypeError, ValueError) as error:
            raise _invalid_token() from error
        if not isinstance(claims, dict):
            raise _invalid_token()
        return self._principal(claims)

    def _principal(self, claims: Mapping[str, Any]) -> ControlPrincipal:
        subject = claims.get("sub")
        raw_roles = claims.get(self.config.role_claim)
        if not isinstance(subject, str) or not subject or len(subject) > 512:
            raise _invalid_token()
        if (
            not isinstance(raw_roles, list)
            or not raw_roles
            or len(raw_roles) > _MAX_ROLES
            or any(not isinstance(role, str) for role in raw_roles)
        ):
            raise _invalid_token()
        roles = tuple(raw_roles)
        capabilities = capabilities_for_roles(roles)
        if self.config.subject_allowlist and subject not in self.config.subject_allowlist:
            raise _not_allowed()
        if self.config.email_allowlist:
            email = claims.get(self.config.email_claim)
            if claims.get("email_verified") is not True or email not in self.config.email_allowlist:
                raise _not_allowed()
        if self.config.group_allowlist:
            groups = claims.get(self.config.group_claim)
            if (
                not isinstance(groups, list)
                or len(groups) > _MAX_GROUPS
                or any(not isinstance(group, str) for group in groups)
                or not set(groups).intersection(self.config.group_allowlist)
            ):
                raise _not_allowed()
        return ControlPrincipal(subject=subject, roles=roles, capabilities=capabilities)


def _bearer_token(authorization: str | None) -> str:
    if authorization is None or len(authorization) > _MAX_BEARER_TOKEN_BYTES + 7:
        raise _invalid_token()
    scheme, separator, token = authorization.partition(" ")
    if (
        scheme.casefold() != "bearer"
        or separator != " "
        or not token
        or any(char.isspace() for char in token)
    ):
        raise _invalid_token()
    if len(token.encode("utf-8")) > _MAX_BEARER_TOKEN_BYTES:
        raise _invalid_token()
    return token


def _invalid_token() -> ControlAuthError:
    return ControlAuthError(
        HTTPStatus.UNAUTHORIZED,
        "authentication_required",
        "A valid hosted Control access token is required.",
    )


def _not_allowed() -> ControlAuthError:
    return ControlAuthError(
        HTTPStatus.FORBIDDEN,
        "identity_not_allowed",
        "The authenticated identity is not allowed to use this deployment.",
    )


def _http_jwks_fetcher(config: HostedOIDCDeploymentInput) -> Callable[[], bytes]:
    def fetch() -> bytes:
        with (
            httpx.Client(
                timeout=config.jwks_timeout_seconds,
                follow_redirects=False,
                trust_env=True,
            ) as client,
            client.stream(
                "GET",
                config.jwks_uri,
                headers={"Accept": "application/json"},
            ) as response,
        ):
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as error:
                    raise JWKSResolutionError(
                        "The signing-key response has an invalid length."
                    ) from error
                if declared < 0 or declared > config.jwks_max_response_bytes:
                    raise JWKSResolutionError("The signing-key response exceeded its bound.")
            body = bytearray()
            for chunk in response.iter_bytes():
                remaining = config.jwks_max_response_bytes + 1 - len(body)
                body.extend(chunk[:remaining])
                if len(body) > config.jwks_max_response_bytes:
                    raise JWKSResolutionError("The signing-key response exceeded its bound.")
            return bytes(body)

    return fetch


def _require_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            "Hosted OIDC URLs must be absolute HTTPS URLs without userinfo or fragments."
        )


def _origin(value: str) -> str:
    _require_https_url(value)
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


__all__ = [
    "BoundedJWKSResolver",
    "ControlAuthError",
    "ControlCapability",
    "ControlPrincipal",
    "HostedOIDCAuthorizer",
    "HostedOIDCDeploymentInput",
    "HostedOIDCProjections",
    "JWKSResolutionError",
    "OIDCServerTrustProjection",
    "PublicOIDCClientProjection",
    "capabilities_for_roles",
    "project_hosted_oidc",
    "verify_oidc_projection_alignment",
]
