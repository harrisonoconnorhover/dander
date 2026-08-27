"""Fail-closed billing checks for strict and guarded GCP sandbox modes."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from dander.identity import google_client_options

_BILLING_SCOPE = "https://www.googleapis.com/auth/cloud-billing.readonly"
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_PROJECT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_MAX_GUARDED_BUDGET_USD = Decimal("5.00")


class SandboxSafetyError(RuntimeError):
    """Raised when Dander cannot prove a project is safe for strict sandbox mode."""


class _Response(Protocol):
    status_code: int

    def json(self) -> object:
        """Decode a JSON response."""


class _Session(Protocol):
    def get(self, url: str, *, timeout: float) -> _Response:
        """Issue an authenticated GET request."""


class _BillingVerifier(Protocol):
    def require_disabled(self, project: str) -> None:
        """Fail unless billing is explicitly disabled."""


class _BillingInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    billing_enabled: StrictBool
    billing_account_name: str = ""


class _Money(BaseModel):
    model_config = ConfigDict(extra="ignore")

    currency_code: str = Field(alias="currencyCode")
    units: str = "0"
    nanos: int = 0


class _BudgetAmount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    specified_amount: _Money = Field(alias="specifiedAmount")


class _ThresholdRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    threshold_percent: float = Field(alias="thresholdPercent")
    spend_basis: str = Field(default="CURRENT_SPEND", alias="spendBasis")


class _NotificationsRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pubsub_topic: str = Field(alias="pubsubTopic")


class _Budget(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str = Field(alias="displayName")
    amount: _BudgetAmount
    threshold_rules: list[_ThresholdRule] = Field(alias="thresholdRules")
    notifications_rule: _NotificationsRule = Field(alias="notificationsRule")


class _BudgetList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    budgets: list[_Budget] = Field(default_factory=list)


class GcpBillingVerifier:
    """Require the Cloud Billing API to explicitly report billing as disabled."""

    def __init__(self, session: _Session | None = None) -> None:
        if session is None:
            try:
                credentials = google_client_options().get("credentials")
                if credentials is None:
                    credentials, _ = google.auth.default(scopes=[_BILLING_SCOPE])
                session = cast(
                    "_Session",
                    AuthorizedSession(credentials),  # type: ignore[no-untyped-call]
                )
            except Exception as error:
                raise SandboxSafetyError(
                    "Application Default Credentials are required to verify billing"
                ) from error
        self._session = session

    def require_disabled(self, project: str) -> None:
        """Fail unless billing is explicitly disabled for ``project``."""
        info = self._billing_info(project)
        if info.billing_enabled:
            raise SandboxSafetyError(
                f"Billing is enabled for project {project!r}; refusing strict $0 sandbox execution"
            )

    def _billing_info(self, project: str) -> _BillingInfo:
        if not _PROJECT_ID.fullmatch(project):
            raise SandboxSafetyError(f"Invalid GCP project id: {project!r}")
        try:
            response = self._session.get(
                f"https://cloudbilling.googleapis.com/v1/projects/{project}/billingInfo",
                timeout=15.0,
            )
        except Exception as error:
            raise SandboxSafetyError(
                "Could not reach Cloud Billing to verify project billing status"
            ) from error
        if response.status_code != 200:
            raise SandboxSafetyError(
                "Could not verify project billing status "
                f"(Cloud Billing API returned HTTP {response.status_code})"
            )
        try:
            payload = response.json()
            info = _BillingInfo.model_validate(
                {
                    "billing_enabled": payload["billingEnabled"],
                    "billing_account_name": payload.get("billingAccountName", ""),
                }
                if isinstance(payload, dict)
                else {}
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            raise SandboxSafetyError(
                "Cloud Billing returned an invalid project billing response"
            ) from error
        return info


class GuardedFreeTierVerifier:
    """Verify billing and conventional budget guardrails before production-path testing."""

    def __init__(self, session: _Session | None = None) -> None:
        if session is None:
            try:
                credentials = google_client_options().get("credentials")
                if credentials is None:
                    credentials, _ = google.auth.default(scopes=[_CLOUD_SCOPE])
                session = cast(
                    "_Session",
                    AuthorizedSession(credentials),  # type: ignore[no-untyped-call]
                )
            except Exception as error:
                raise SandboxSafetyError(
                    "Application Default Credentials are required to verify cost guardrails"
                ) from error
        self._session = session

    def require_guarded(
        self,
        project: str,
        *,
        budget_name: str = "dander-sbx-cap",
    ) -> None:
        """Fail unless the billing-linked project has Dander's expected budget guardrails."""
        billing = GcpBillingVerifier(self._session)._billing_info(project)
        if not billing.billing_enabled or not billing.billing_account_name:
            raise SandboxSafetyError(
                f"Billing is not enabled for project {project!r}; use --sandbox instead"
            )
        account = billing.billing_account_name
        budget_payload = self._get_json(
            "https://billingbudgets.googleapis.com/v1/"
            f"{account}/budgets?scope=projects/{project}&pageSize=100",
            label="Cloud Billing Budget",
        )
        try:
            budgets = _BudgetList.model_validate(budget_payload).budgets
        except ValidationError as error:
            raise SandboxSafetyError("Cloud Billing Budget returned an invalid response") from error
        budget = next((item for item in budgets if item.display_name == budget_name), None)
        if budget is None:
            raise SandboxSafetyError(
                f"Project-scoped budget {budget_name!r} was not found for project {project!r}"
            )
        self._validate_budget(budget, project)

    def _validate_budget(self, budget: _Budget, project: str) -> None:
        money = budget.amount.specified_amount
        try:
            amount = Decimal(money.units) + (Decimal(money.nanos) / Decimal(1_000_000_000))
        except (InvalidOperation, ValueError) as error:
            raise SandboxSafetyError("Budget has an invalid specified amount") from error
        if money.currency_code != "USD" or amount <= 0 or amount > _MAX_GUARDED_BUDGET_USD:
            raise SandboxSafetyError(
                f"Budget must be USD and no greater than {_MAX_GUARDED_BUDGET_USD:.2f}; found "
                f"{money.currency_code} {amount}"
            )

        current_thresholds = {
            Decimal(str(rule.threshold_percent))
            for rule in budget.threshold_rules
            if rule.spend_basis == "CURRENT_SPEND"
        }
        if not {Decimal("0.8"), Decimal("1.0")}.issubset(current_thresholds):
            raise SandboxSafetyError("Budget must have 80% and 100% current-spend threshold rules")

        topic = f"projects/{project}/topics/dander-stop-billing"
        if budget.notifications_rule.pubsub_topic != topic:
            raise SandboxSafetyError(f"Budget must publish updates to {topic}")
        subscriptions = self._get_json(
            f"https://pubsub.googleapis.com/v1/{topic}/subscriptions?pageSize=100",
            label="Pub/Sub",
        )
        attached = subscriptions.get("subscriptions") if isinstance(subscriptions, dict) else None
        if not isinstance(attached, list) or not attached:
            raise SandboxSafetyError("Budget topic must have an attached kill-switch subscription")

    def _get_json(self, url: str, *, label: str) -> object:
        try:
            response = self._session.get(url, timeout=15.0)
        except Exception as error:
            raise SandboxSafetyError(f"Could not reach {label} API") from error
        if response.status_code != 200:
            raise SandboxSafetyError(
                f"Could not verify {label} configuration (HTTP {response.status_code})"
            )
        try:
            return response.json()
        except ValueError as error:
            raise SandboxSafetyError(f"{label} returned invalid JSON") from error


class SandboxDataset:
    """Create the raw dataset only after the no-billing safety check passes."""

    def __init__(
        self,
        *,
        verifier: _BillingVerifier | None = None,
        client: object | None = None,
    ) -> None:
        self._verifier = verifier or GcpBillingVerifier()
        self._client = cast("bigquery.Client | None", client)

    def prepare(self, project: str, dataset: str) -> None:
        """Verify billing is disabled, then create the dataset if absent."""
        self._verifier.require_disabled(project)
        if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", dataset):
            raise SandboxSafetyError(f"Invalid BigQuery dataset id: {dataset!r}")
        resource = bigquery.Dataset(f"{project}.{dataset}")
        client = self._client or bigquery.Client(project=project)
        try:
            client.create_dataset(resource, exists_ok=True)
        except Exception as error:
            raise SandboxSafetyError(
                f"Could not create or access BigQuery dataset {project}.{dataset}"
            ) from error
