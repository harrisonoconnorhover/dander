"""Security module: secret resolution and pluggable authentication strategies."""

from dander.security.api_bearer import ApiKeyBearer
from dander.security.api_key import ApiKeyBasic
from dander.security.base import AuthStrategy
from dander.security.no_auth import NoAuth
from dander.security.oauth import (
    ClientCredentialPlacement,
    OAuth2ClientCredentials,
    OAuthTokenError,
)
from dander.security.oauth1 import OAuth1TBA
from dander.security.oauth_jwt import OAuth2JWT
from dander.security.runtime import SecretCapabilities, SecretRuntime
from dander.security.secret_manager import (
    DefaultSecretStore,
    EnvironmentSecretStore,
    GcpSecretStore,
    SecretResolutionError,
)

__all__ = [
    "ApiKeyBasic",
    "ApiKeyBearer",
    "AuthStrategy",
    "ClientCredentialPlacement",
    "DefaultSecretStore",
    "EnvironmentSecretStore",
    "GcpSecretStore",
    "NoAuth",
    "OAuth2ClientCredentials",
    "OAuth2JWT",
    "OAuth1TBA",
    "OAuthTokenError",
    "SecretResolutionError",
    "SecretCapabilities",
    "SecretRuntime",
]
