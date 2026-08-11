"""Adapt Azure Container Apps managed identity for Google workload federation."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from google.auth import external_account

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping
    from typing import Any

_WIF_AUDIENCE = re.compile(
    r"^//iam\.googleapis\.com/projects/[0-9]{6,20}/locations/global/"
    r"workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/[a-z][a-z0-9-]{3,31}$"
)
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
    r"\.iam\.gserviceaccount\.com$"
)
_AZURE_APPLICATION_ID_URI = re.compile(
    r"^api://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_JWT_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class AzureContainerAppsIdentityError(RuntimeError):
    """Azure managed identity is missing, stale, or unsafe for Google federation."""


class AzureAccessToken(Protocol):
    """Minimum token shape returned by ``azure-identity``."""

    token: str
    expires_on: int


class AzureTokenCredential(Protocol):
    """Minimum managed-identity credential boundary used by the adapter."""

    def get_token(self, *scopes: str) -> AzureAccessToken:
        """Return an access token for the requested Microsoft Entra scope."""


class AzureCredentialFactory(Protocol):
    def __call__(self, *, client_id: str) -> AzureTokenCredential:
        """Build one user-assigned managed-identity credential."""


class GoogleCredentialFactory(Protocol):
    def __call__(
        self,
        *,
        audience: str,
        service_account: str,
        supplier: _AzureSubjectTokenSupplier,
    ) -> object:
        """Build renewable Google credentials from one Azure subject-token supplier."""


class _AzureSubjectTokenSupplier:
    """Request a fresh Entra token whenever Google Auth refreshes."""

    def __init__(
        self,
        *,
        credential: AzureTokenCredential,
        application_id_uri: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._credential = credential
        self._scope = f"{application_id_uri}/.default"
        self._clock = clock

    def validate(self) -> None:
        """Fail before provider construction if managed identity is unusable."""
        self.subject_token()

    def subject_token(self) -> str:
        """Return one non-persisted Entra JWT for Google's token exchange."""
        try:
            access_token = self._credential.get_token(self._scope)
        except Exception as error:  # noqa: BLE001 - Azure SDK errors can vary by version.
            raise AzureContainerAppsIdentityError(
                "Azure Container Apps managed identity token was unavailable"
            ) from error
        token = access_token.token
        expiry = datetime.fromtimestamp(access_token.expires_on, tz=UTC)
        if (
            not isinstance(token, str)
            or _JWT.fullmatch(token) is None
            or expiry <= self._clock().astimezone(UTC) + timedelta(minutes=5)
        ):
            raise AzureContainerAppsIdentityError(
                "Azure Container Apps managed identity token was invalid"
            )
        return token


class _AzureGoogleCredentials(external_account.Credentials):
    """Google external-account credentials backed by the Container Apps identity SDK."""

    def __init__(
        self,
        *,
        audience: str,
        supplier: _AzureSubjectTokenSupplier,
        service_account: str | None = None,
        subject_token_type: str = _JWT_SUBJECT_TOKEN_TYPE,
        token_url: str = "https://sts.googleapis.com/v1/token",
        credential_source: dict[str, object] | None = None,
        service_account_impersonation_url: str | None = None,
        service_account_impersonation_options: dict[str, object] | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_info_url: str | None = None,
        quota_project_id: str | None = None,
        scopes: tuple[str, ...] | None = None,
        default_scopes: tuple[str, ...] | None = None,
        workforce_pool_user_project: str | None = None,
        universe_domain: str = "googleapis.com",
        trust_boundary: dict[str, str] | None = None,
    ) -> None:
        self._azure_supplier = supplier
        if service_account is not None:
            service_account_impersonation_url = (
                "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                f"{service_account}:generateAccessToken"
            )
            service_account_impersonation_options = {"token_lifetime_seconds": 600}
        credential_source = credential_source or {"environment_id": "azure-container-apps"}
        scopes = scopes or (_CLOUD_PLATFORM_SCOPE,)
        super().__init__(  # type: ignore[no-untyped-call]
            audience=audience,
            subject_token_type=subject_token_type,
            token_url=token_url,
            credential_source=credential_source,
            service_account_impersonation_url=service_account_impersonation_url,
            service_account_impersonation_options=service_account_impersonation_options,
            client_id=client_id,
            client_secret=client_secret,
            token_info_url=token_info_url,
            quota_project_id=quota_project_id,
            scopes=scopes,
            default_scopes=default_scopes,
            workforce_pool_user_project=workforce_pool_user_project,
            universe_domain=universe_domain,
            trust_boundary=trust_boundary,
        )

    def _constructor_args(self) -> dict[str, Any]:
        """Preserve the Azure supplier when Google Auth clones source credentials."""
        kwargs = super()._constructor_args()  # type: ignore[no-untyped-call]
        kwargs["supplier"] = self._azure_supplier
        return cast("dict[str, Any]", kwargs)

    def retrieve_subject_token(self, request: object) -> str:
        """Supply a fresh Entra token without writing a credential file."""
        del request
        return self._azure_supplier.subject_token()


def prepare_azure_google_identity(
    *,
    environ: MutableMapping[str, str] = os.environ,
    clock: Callable[[], datetime] | None = None,
    azure_credential_factory: AzureCredentialFactory | None = None,
    google_credential_factory: GoogleCredentialFactory | None = None,
) -> object:
    """Build renewable Google credentials from Azure user-assigned managed identity."""
    audience = environ.get("DANDER_GCP_WIF_AUDIENCE", "")
    service_account = environ.get("DANDER_GCP_SERVICE_ACCOUNT", "")
    application_id_uri = environ.get("DANDER_AZURE_GCP_APPLICATION_ID_URI", "")
    client_id = environ.get("AZURE_CLIENT_ID", "")
    if (
        _WIF_AUDIENCE.fullmatch(audience) is None
        or _SERVICE_ACCOUNT.fullmatch(service_account) is None
        or _AZURE_APPLICATION_ID_URI.fullmatch(application_id_uri) is None
        or not _rfc4122_uuid(client_id)
    ):
        raise AzureContainerAppsIdentityError(
            "Azure-to-Google workload identity configuration is invalid"
        )
    credential_factory = azure_credential_factory or _build_azure_credential
    supplier = _AzureSubjectTokenSupplier(
        credential=credential_factory(client_id=client_id),
        application_id_uri=application_id_uri,
        clock=clock or (lambda: datetime.now(UTC)),
    )
    supplier.validate()
    factory = google_credential_factory or _build_google_credentials
    return factory(
        audience=audience,
        service_account=service_account,
        supplier=supplier,
    )


def _build_azure_credential(*, client_id: str) -> AzureTokenCredential:
    try:
        identity_module = import_module("azure.identity")
    except ModuleNotFoundError as error:  # pragma: no cover - packaging contract covers this path
        raise AzureContainerAppsIdentityError(
            "Azure managed-identity dependencies are unavailable"
        ) from error
    credential = cast("Any", identity_module).ManagedIdentityCredential(client_id=client_id)
    return cast("AzureTokenCredential", credential)


def _build_google_credentials(
    *,
    audience: str,
    service_account: str,
    supplier: _AzureSubjectTokenSupplier,
) -> object:
    return _AzureGoogleCredentials(
        audience=audience,
        service_account=service_account,
        supplier=supplier,
    )


def _rfc4122_uuid(value: str) -> bool:
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.variant == "specified in RFC 4122"


__all__ = ["AzureContainerAppsIdentityError", "prepare_azure_google_identity"]
