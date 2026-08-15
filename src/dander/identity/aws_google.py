"""Adapt renewable Fargate task-role credentials for Google workload federation."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, MutableMapping
    from typing import Any

    from dander.runtime_contract import LauncherContext

_ECS_CREDENTIALS_PATH = re.compile(r"^/v2/credentials/[A-Za-z0-9-]{16,128}$")
_ECS_CREDENTIALS_ORIGIN = "http://169.254.170.2"
_TEMPORARY_ACCESS_KEY = re.compile(r"^ASIA[A-Z0-9]{16}$")
_AWS_CREDENTIAL_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_GOOGLE_FEDERATION_ENV = (
    "DANDER_GCP_SERVICE_ACCOUNT",
    "DANDER_GCP_WIF_AUDIENCE",
)
_WIF_AUDIENCE = re.compile(
    r"^//iam\.googleapis\.com/projects/[0-9]{6,20}/locations/global/"
    r"workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/[a-z][a-z0-9-]{3,31}$"
)
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
    r"\.iam\.gserviceaccount\.com$"
)
_AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_RUNTIME_GOOGLE_CREDENTIALS: ContextVar[object | None] = ContextVar(
    "dander_runtime_google_credentials",
    default=None,
)


class FargateIdentityError(RuntimeError):
    """Fargate task identity is missing, stale, or unsafe for federation."""


class CredentialFetcher(Protocol):
    def __call__(self, url: str) -> object:
        """Return one parsed ECS task-credential response."""


class GoogleCredentialFactory(Protocol):
    def __call__(
        self,
        *,
        audience: str,
        service_account: str,
        supplier: _FargateCredentialSupplier,
    ) -> object:
        """Build renewable Google credentials from one task-role supplier."""


def _fetch_ecs_credentials(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise FargateIdentityError("Fargate task credentials were unavailable")
        return json.load(response)


class _FargateCredentialSupplier:
    """Supply a fresh ECS task-role session whenever Google Auth refreshes."""

    def __init__(
        self,
        *,
        environ: MutableMapping[str, str],
        fetch: CredentialFetcher,
        clock: Callable[[], datetime],
    ) -> None:
        self._environ = environ
        self._fetch = fetch
        self._clock = clock
        self._relative_uri = _validated_relative_uri(environ)
        self._region = _validated_region(environ)
        _reject_preconfigured_credentials(environ)

    def validate(self) -> None:
        """Fail before provider construction if the current task session is unusable."""
        self._current_values()

    def get_aws_region(self, context: object, request: object) -> str:
        """Return the validated task region without provider metadata calls."""
        del context, request
        return self._region

    def get_aws_security_credentials(self, context: object, request: object) -> object:
        """Fetch the current ECS session for one Google subject-token refresh."""
        del context, request
        access_key, secret_key, session_token = self._current_values()
        try:
            from google.auth.aws import AwsSecurityCredentials
        except ImportError as error:  # pragma: no cover - packaging contract covers this path
            raise FargateIdentityError(
                "Fargate Google workload identity dependencies are unavailable"
            ) from error
        return AwsSecurityCredentials(access_key, secret_key, session_token)

    def _current_values(self) -> tuple[str, str, str]:
        document = self._fetch(f"{_ECS_CREDENTIALS_ORIGIN}{self._relative_uri}")
        return _validated_credentials(document, current=self._clock().astimezone(UTC))


@contextmanager
def launcher_identity(context: LauncherContext) -> Iterator[None]:
    """Scope launcher-specific credentials to one runtime execution."""
    credentials = prepare_launcher_identity(context)
    token = _RUNTIME_GOOGLE_CREDENTIALS.set(credentials)
    try:
        yield
    finally:
        _RUNTIME_GOOGLE_CREDENTIALS.reset(token)


def prepare_launcher_identity(context: LauncherContext) -> object | None:
    """Prepare only the selected launcher's ambient identity before clients start."""
    google_federation_configured = any(os.environ.get(name) for name in _GOOGLE_FEDERATION_ENV)
    if context.launcher == "fargate" and google_federation_configured:
        return prepare_fargate_google_identity()
    if context.launcher == "azure_container_apps" and any(
        os.environ.get(name)
        for name in (
            "DANDER_AZURE_GCP_APPLICATION_ID_URI",
            *_GOOGLE_FEDERATION_ENV,
        )
    ):
        from dander.identity.azure_google import prepare_azure_google_identity

        return prepare_azure_google_identity()
    return None


def google_client_options() -> dict[str, Any]:
    """Return explicit Google client credentials only inside a launcher identity scope."""
    credentials = _RUNTIME_GOOGLE_CREDENTIALS.get()
    return {} if credentials is None else {"credentials": credentials}


def prepare_fargate_google_identity(
    *,
    environ: MutableMapping[str, str] = os.environ,
    fetch: CredentialFetcher = _fetch_ecs_credentials,
    clock: Callable[[], datetime] | None = None,
    credential_factory: GoogleCredentialFactory | None = None,
) -> object:
    """Build renewable Google credentials without copying or persisting task secrets."""
    audience = environ.get("DANDER_GCP_WIF_AUDIENCE", "")
    service_account = environ.get("DANDER_GCP_SERVICE_ACCOUNT", "")
    if (
        _WIF_AUDIENCE.fullmatch(audience) is None
        or _SERVICE_ACCOUNT.fullmatch(service_account) is None
    ):
        raise FargateIdentityError("Fargate Google workload identity is invalid")
    supplier = _FargateCredentialSupplier(
        environ=environ,
        fetch=fetch,
        clock=clock or (lambda: datetime.now(UTC)),
    )
    supplier.validate()
    factory = credential_factory or _build_google_credentials
    return factory(
        audience=audience,
        service_account=service_account,
        supplier=supplier,
    )


def _build_google_credentials(
    *,
    audience: str,
    service_account: str,
    supplier: _FargateCredentialSupplier,
) -> object:
    try:
        from google.auth.aws import Credentials
    except ImportError as error:  # pragma: no cover - packaging contract covers this path
        raise FargateIdentityError(
            "Fargate Google workload identity dependencies are unavailable"
        ) from error
    return Credentials(  # type: ignore[no-untyped-call]
        audience=audience,
        subject_token_type=_AWS_SUBJECT_TOKEN_TYPE,
        token_url="https://sts.googleapis.com/v1/token",
        aws_security_credentials_supplier=supplier,
        service_account_impersonation_url=(
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{service_account}:generateAccessToken"
        ),
        service_account_impersonation_options={"token_lifetime_seconds": 600},
        scopes=(_CLOUD_PLATFORM_SCOPE,),
    )


def prepare_fargate_task_credentials(
    *,
    environ: MutableMapping[str, str] = os.environ,
    fetch: CredentialFetcher = _fetch_ecs_credentials,
    now: datetime | None = None,
) -> bool:
    """Expose one bounded ECS session for the retained Phase 1B probe helper."""
    _reject_preconfigured_credentials(environ)
    relative_uri = _validated_relative_uri(environ)
    values = _validated_credentials(
        fetch(f"{_ECS_CREDENTIALS_ORIGIN}{relative_uri}"),
        current=(now or datetime.now(UTC)).astimezone(UTC),
    )
    for name, value in zip(_AWS_CREDENTIAL_NAMES, values, strict=True):
        environ[name] = value
    return True


def _reject_preconfigured_credentials(environ: MutableMapping[str, str]) -> None:
    if any(environ.get(name) for name in _AWS_CREDENTIAL_NAMES):
        raise FargateIdentityError("Fargate does not accept preconfigured AWS credentials")


def _validated_relative_uri(environ: MutableMapping[str, str]) -> str:
    relative_uri = environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if relative_uri is None or _ECS_CREDENTIALS_PATH.fullmatch(relative_uri) is None:
        raise FargateIdentityError("Fargate task credential endpoint is unavailable")
    return relative_uri


def _validated_region(environ: MutableMapping[str, str]) -> str:
    region = environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION", "")
    if _AWS_REGION.fullmatch(region) is None:
        raise FargateIdentityError("Fargate task region is invalid")
    return region


def _validated_credentials(document: object, *, current: datetime) -> tuple[str, str, str]:
    if not isinstance(document, dict):
        raise FargateIdentityError("Fargate task credentials were invalid")
    access_key = document.get("AccessKeyId")
    secret_key = document.get("SecretAccessKey")
    session_token = document.get("Token")
    expiration = _expiration(document.get("Expiration"))
    if (
        not isinstance(access_key, str)
        or _TEMPORARY_ACCESS_KEY.fullmatch(access_key) is None
        or not isinstance(secret_key, str)
        or not secret_key
        or not isinstance(session_token, str)
        or not session_token
        or expiration <= current + timedelta(minutes=5)
    ):
        raise FargateIdentityError("Fargate task credentials were invalid")
    return access_key, secret_key, session_token


def _expiration(value: object) -> datetime:
    if not isinstance(value, str):
        raise FargateIdentityError("Fargate task credentials were invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FargateIdentityError("Fargate task credentials were invalid") from error
    if parsed.tzinfo is None:
        raise FargateIdentityError("Fargate task credentials were invalid")
    return parsed.astimezone(UTC)
