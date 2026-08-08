"""Adapt short-lived Fargate task-role credentials for Google workload federation."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from dander.runtime_contract import LauncherContext

_ECS_CREDENTIALS_PATH = re.compile(r"^/v2/credentials/[A-Za-z0-9-]{16,128}$")
_ECS_CREDENTIALS_ORIGIN = "http://169.254.170.2"
_TEMPORARY_ACCESS_KEY = re.compile(r"^ASIA[A-Z0-9]{16}$")
_AWS_CREDENTIAL_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
_WIF_AUDIENCE = re.compile(
    r"^//iam\.googleapis\.com/projects/[0-9]{6,20}/locations/global/"
    r"workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/[a-z][a-z0-9-]{3,31}$"
)
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
    r"\.iam\.gserviceaccount\.com$"
)
_DEFAULT_CREDENTIAL_PATH = Path("/tmp/dander-gcp-wif.json")


class FargateIdentityError(RuntimeError):
    """Fargate task identity is missing, stale, or unsafe for federation."""


class CredentialFetcher(Protocol):
    def __call__(self, url: str) -> object:
        """Return one parsed ECS task-credential response."""


def _fetch_ecs_credentials(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise FargateIdentityError("Fargate task credentials were unavailable")
        return json.load(response)


def prepare_launcher_identity(context: LauncherContext) -> None:
    """Prepare only the selected launcher's ambient identity before provider clients start."""
    if context.launcher == "fargate":
        prepare_fargate_google_identity()


def prepare_fargate_google_identity(
    *,
    environ: MutableMapping[str, str] = os.environ,
    fetch: CredentialFetcher = _fetch_ecs_credentials,
    credential_path: Path = _DEFAULT_CREDENTIAL_PATH,
    now: datetime | None = None,
) -> None:
    """Prepare task-role credentials and a non-secret Google external-account file."""
    audience = environ.get("DANDER_GCP_WIF_AUDIENCE", "")
    service_account = environ.get("DANDER_GCP_SERVICE_ACCOUNT", "")
    if (
        _WIF_AUDIENCE.fullmatch(audience) is None
        or _SERVICE_ACCOUNT.fullmatch(service_account) is None
    ):
        raise FargateIdentityError("Fargate Google workload identity is invalid")
    prepare_fargate_task_credentials(environ=environ, fetch=fetch, now=now)
    config: dict[str, object] = {
        "audience": audience,
        "credential_source": {
            "environment_id": "aws1",
            "region_url": ("http://169.254.169.254/latest/meta-data/placement/availability-zone"),
            "regional_cred_verification_url": (
                "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
            ),
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
        },
        "service_account_impersonation": {"token_lifetime_seconds": 600},
        "service_account_impersonation_url": (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{service_account}:generateAccessToken"
        ),
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "type": "external_account",
    }
    temporary = credential_path.with_suffix(credential_path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, credential_path)
    except OSError as error:
        raise FargateIdentityError(
            "Fargate Google workload identity configuration could not be prepared"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)
    environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credential_path)


def prepare_fargate_task_credentials(
    *,
    environ: MutableMapping[str, str] = os.environ,
    fetch: CredentialFetcher = _fetch_ecs_credentials,
    now: datetime | None = None,
) -> bool:
    """Expose one bounded ECS task-role session to Google Auth in the current process."""
    existing = tuple(environ.get(name) for name in _AWS_CREDENTIAL_NAMES)
    if any(existing):
        raise FargateIdentityError("Fargate does not accept preconfigured AWS credentials")

    relative_uri = environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if relative_uri is None or _ECS_CREDENTIALS_PATH.fullmatch(relative_uri) is None:
        raise FargateIdentityError("Fargate task credential endpoint is unavailable")
    document = fetch(f"{_ECS_CREDENTIALS_ORIGIN}{relative_uri}")
    if not isinstance(document, dict):
        raise FargateIdentityError("Fargate task credentials were invalid")
    access_key = document.get("AccessKeyId")
    secret_key = document.get("SecretAccessKey")
    session_token = document.get("Token")
    expiration = _expiration(document.get("Expiration"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
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

    environ["AWS_SECRET_ACCESS_KEY"] = secret_key
    environ["AWS_SESSION_TOKEN"] = session_token
    environ["AWS_ACCESS_KEY_ID"] = access_key
    return True


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
