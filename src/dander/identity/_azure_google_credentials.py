"""Google credentials loaded only for the selected Azure-to-Google identity path."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from google.auth import external_account

if TYPE_CHECKING:
    from typing import Any

    from dander.identity.azure_google import _AzureSubjectTokenSupplier

_JWT_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


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
