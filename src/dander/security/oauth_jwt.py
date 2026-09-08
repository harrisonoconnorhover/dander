"""OAuth 2.0 JWT-bearer authentication with secret-backed signing."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import monotonic, time
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import jwt

from dander.security.base import AuthStrategy
from dander.security.oauth import OAuthTokenError

if TYPE_CHECKING:
    from dander.core.interfaces import SecretStoreProvider


class JwtTokenRequester(Protocol):
    """Injectable boundary for the JWT bearer token exchange."""

    def __call__(
        self,
        url: str,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Send one form-encoded token request."""


class AssertionSigner(Protocol):
    """Injectable trusted JWT signing boundary."""

    def __call__(
        self,
        *,
        issuer: str,
        private_key: str,
        audience: str,
        scope: str | None,
        subject: str | None,
        issued_at: int,
        assertion_lifetime: int,
    ) -> str:
        """Return one signed JWT assertion."""


class OAuth2JWT(AuthStrategy):
    """Apply a cached OAuth bearer token obtained with a signed JWT assertion."""

    def __init__(
        self,
        secrets: SecretStoreProvider,
        *,
        issuer_ref: str,
        private_key_ref: str,
        token_url: str,
        audience: str | None = None,
        scope: str | None = None,
        subject: str | None = None,
        assertion_lifetime: int = 3600,
        default_expires_in: int = 300,
        request_token: JwtTokenRequester | None = None,
        sign_assertion: AssertionSigner | None = None,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], float] = time,
    ) -> None:
        super().__init__(secrets, private_key_ref)
        if not token_url.startswith("https://"):
            raise ValueError("OAuth JWT token URL must use HTTPS")
        if audience is not None and not audience.startswith("https://"):
            raise ValueError("OAuth JWT audience must use HTTPS")
        if scope is not None and not scope.strip():
            raise ValueError("OAuth JWT scope must be non-empty when set")
        if not 60 <= default_expires_in <= 3600:
            raise ValueError("OAuth JWT default expiry must be between 60 and 3600 seconds")
        if not 60 <= assertion_lifetime <= 3600:
            raise ValueError("OAuth JWT assertion lifetime must be between 60 and 3600 seconds")
        self._issuer_ref = issuer_ref
        self._private_key_ref = private_key_ref
        self._token_url = token_url
        self._audience = audience or token_url
        self._scope = scope
        self._subject = subject
        self._assertion_lifetime = assertion_lifetime
        self._default_expires_in = default_expires_in
        self._request_token = request_token or _post_token
        self._sign_assertion = sign_assertion or _sign_rs256
        self._clock = clock
        self._wall_clock = wall_clock
        self._access_token: str | None = None
        self._expires_at = 0.0

    def apply(self, request: httpx.Request) -> httpx.Request:
        """Attach a current bearer token to the request."""
        if self._access_token is None or self._clock() >= self._expires_at:
            self._obtain_token()
        request.headers["Authorization"] = f"Bearer {self._access_token}"
        return request

    def refresh(self) -> None:
        """Discard the cached bearer token."""
        self._access_token = None
        self._expires_at = 0.0

    def _obtain_token(self) -> None:
        issuer = self._secrets.get_secret(self._issuer_ref)
        private_key = self._secrets.get_secret(self._private_key_ref)
        try:
            assertion = self._sign_assertion(
                issuer=issuer,
                private_key=private_key,
                audience=self._audience,
                scope=self._scope,
                subject=self._subject,
                issued_at=int(self._wall_clock()),
                assertion_lifetime=self._assertion_lifetime,
            )
            response = self._request_token(
                self._token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, jwt.InvalidKeyError, ValueError) as error:
            raise OAuthTokenError("OAuth JWT token exchange failed") from error

        if not isinstance(payload, Mapping):
            raise OAuthTokenError("OAuth JWT token response must be a JSON object")
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", self._default_expires_in)
        if not isinstance(access_token, str) or not access_token:
            raise OAuthTokenError("OAuth JWT token response omitted access_token")
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
            raise OAuthTokenError("OAuth JWT token response omitted numeric expires_in")
        if expires_in <= 0:
            raise OAuthTokenError("OAuth JWT token expiry must be positive")
        self._access_token = access_token
        lifetime = float(expires_in)
        self._expires_at = self._clock() + max(lifetime - 30.0, lifetime * 0.9)


def _sign_rs256(
    *,
    issuer: str,
    private_key: str,
    audience: str,
    scope: str | None,
    subject: str | None,
    issued_at: int,
    assertion_lifetime: int,
) -> str:
    """Sign a standards-shaped JWT assertion with an RSA private key."""
    payload: dict[str, str | int] = {
        "iss": issuer,
        "aud": audience,
        "iat": issued_at,
        "exp": issued_at + assertion_lifetime,
    }
    if scope is not None:
        payload["scope"] = scope
    if subject is not None:
        payload["sub"] = subject
    return jwt.encode(payload, private_key, algorithm="RS256")


def _post_token(
    url: str,
    *,
    data: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> httpx.Response:
    return httpx.post(url, data=data, headers=headers, timeout=timeout)
