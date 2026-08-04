"""The ``Source`` abstraction — the common contract for BOTH ingestion paths.

Per the hybrid decision (see the Decision Log in ``steering/00-project-overview.md``): standard
REST sources are implemented on dlt; gnarly enterprise sources (Workday, NetSuite, Xactly) are
hand-rolled. Both implement ``Source``, so the writer, state, and orchestration layers never care
which path produced the records.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dander.ingestion.pagination import NoPagination, PaginationStrategy
from dander.schema import BIGQUERY_FIELD_MODES, BIGQUERY_FIELD_TYPES, normalize_bigquery_type

_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class RawField(BaseModel):
    """One complete field declaration for a raw BigQuery relation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    data_type: str = Field(alias="type")
    mode: str = "NULLABLE"
    fields: list[RawField] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _FIELD_NAME.fullmatch(value):
            raise ValueError("raw schema field names must be BigQuery identifiers")
        return value

    @field_validator("data_type")
    @classmethod
    def _validate_data_type(cls, value: str) -> str:
        normalized = normalize_bigquery_type(value)
        if normalized not in BIGQUERY_FIELD_TYPES:
            raise ValueError(f"unsupported raw schema type: {value}")
        return normalized

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in BIGQUERY_FIELD_MODES:
            raise ValueError(f"unsupported raw schema mode: {value}")
        return normalized

    @model_validator(mode="after")
    def _validate_nested_contract(self) -> RawField:
        if self.data_type == "RECORD" and not self.fields:
            raise ValueError("RECORD raw schema fields must declare nested fields")
        if self.data_type != "RECORD" and self.fields:
            raise ValueError("only RECORD raw schema fields can declare nested fields")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("nested raw schema field names must be unique")
        return self


class Endpoint(BaseModel):
    """One retrievable entity within a source (loaded from the connector YAML).

    Attributes:
        name: Endpoint identifier, unique within its `SourceConfig`.
        path: Path appended to `SourceConfig.base_url` to retrieve this endpoint.
        pagination: The pagination strategy for this endpoint (see
            `dander.ingestion.pagination.PaginationStrategy`), mirroring the strategy pattern
            already used for auth (`steering/01-security.md`). Defaults to `NoPagination` — a
            single request, no paging. A bare string (e.g. ``"link_header"``) is accepted as
            shorthand for ``{"kind": "link_header"}`` and coerced to the matching strategy; this
            keeps existing connector YAML (`connectors/greenhouse.yaml`) loading unchanged.
        incremental_cursor: Response field name used as the incremental watermark, or `None` for
            a full-refresh endpoint. **Legacy, narrow form (pre-DANDER-18):** just a field name,
            with no cursor *kind* (timestamp / sequence / opaque token) and disconnected from the
            pipeline graph model. Superseded by the node-level `dander.pipeline.graph.
            CursorStrategy` (attached via `Node.cursor`), which adds the kind plus boundary
            validation and is surfaced at the graph level per `steering/00-project-overview.md`'s
            control-table/idempotency design. Kept here unchanged so existing connector YAML
            (e.g. `connectors/greenhouse.yaml`) keeps loading; a caller that wants a
            `CursorStrategy` from a legacy endpoint should call
            `CursorStrategy.from_incremental_cursor(endpoint.incremental_cursor)` rather than
            reading this field directly for new code.
        cursor_param: Optional request query-parameter name that receives the persisted cursor.
            Defaults to `incremental_cursor` when omitted. This distinction is necessary for APIs
            such as Greenhouse, whose response field is `updated_at` while its filter parameter is
            `updated_after`.
        primary_key: Field name(s) forming this endpoint's business key.
        data_selector: Optional JSONPath selecting records from an enveloped response.
        query_params: Non-secret scalar query parameters applied to every request. Authentication
            material must use the source's auth strategy instead.
        request_body: Non-secret scalar JSON body applied by a bespoke enterprise source. The
            generic dlt adapter rejects this field rather than silently ignoring it.
        raw_schema: Complete raw relation schema for hosted project execution. Direct-source
            callers may omit it temporarily through the deprecated inference path.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    path: str
    pagination: PaginationStrategy = Field(default_factory=NoPagination)
    incremental_cursor: str | None = None
    cursor_param: str | None = None
    primary_key: list[str] = Field(default_factory=list)
    data_selector: str | None = None
    query_params: dict[str, str | int | bool] = Field(default_factory=dict)
    request_body: dict[str, str | int | bool] = Field(default_factory=dict)
    field_types: dict[str, str] = Field(
        default_factory=dict,
        description="Explicit BigQuery type overrides applied by hand-rolled enterprise sources.",
    )
    raw_schema: list[RawField] = Field(
        default_factory=list,
        description="Complete raw BigQuery schema for empty and sparse endpoint results.",
    )

    @field_validator("field_types")
    @classmethod
    def _validate_field_types(cls, value: dict[str, str]) -> dict[str, str]:
        """Restrict enterprise casts to supported BigQuery scalar types."""
        supported = {"BOOL", "DATE", "FLOAT64", "INT64", "NUMERIC", "STRING", "TIMESTAMP"}
        normalized = {field: data_type.upper() for field, data_type in value.items()}
        if any(not _FIELD_NAME.fullmatch(field) for field in normalized):
            raise ValueError("field_types keys must be field identifiers")
        if unknown := sorted(set(normalized.values()) - supported):
            raise ValueError(f"Unsupported field type: {unknown[0]}")
        return normalized

    @field_validator("query_params", "request_body")
    @classmethod
    def _validate_static_request_values(
        cls, value: dict[str, str | int | bool]
    ) -> dict[str, str | int | bool]:
        """Keep static request values non-secret and request-safe."""
        sensitive_names = {
            "api_key",
            "apikey",
            "authorization",
            "client_secret",
            "credential",
            "key",
            "password",
            "secret",
            "token",
        }
        for name in value:
            normalized = name.lower().replace("-", "_")
            if not name or "\r" in name or "\n" in name:
                raise ValueError("static request keys must be non-empty single-line names")
            if normalized in sensitive_names or normalized.endswith(("_key", "_secret", "_token")):
                raise ValueError(
                    f"static requests cannot contain credential-like parameter {name!r}; "
                    "use auth_strategy"
                )
        return value

    @model_validator(mode="after")
    def _validate_raw_schema(self) -> Endpoint:
        if not self.raw_schema:
            return self
        names = [field.name for field in self.raw_schema]
        if len(names) != len(set(names)):
            raise ValueError("raw schema field names must be unique")
        declared = set(names)
        if missing_keys := sorted(set(self.primary_key) - declared):
            raise ValueError(f"raw schema is missing primary-key field {missing_keys[0]!r}")
        if self.incremental_cursor is not None and self.incremental_cursor not in declared:
            raise ValueError(
                f"raw schema is missing incremental cursor {self.incremental_cursor!r}"
            )
        return self

    @field_validator("pagination", mode="before")
    @classmethod
    def _coerce_bare_pagination_kind(cls, value: object) -> object:
        """Coerce a bare pagination-kind string into its object shorthand.

        Lets existing connector YAML keep writing ``pagination: none`` / ``pagination:
        link_header`` rather than requiring the full ``{"kind": ...}`` object form for every
        endpoint. Dicts and already-typed strategy instances pass through untouched; the
        discriminated union in `dander.ingestion.pagination.PaginationStrategy` still rejects an
        out-of-set kind or a kind missing a required param (e.g. `cursor` without
        `next_cursor_path`, which has no safe universal default and so has no shorthand).

        Args:
            value: The raw `pagination` value being validated.

        Returns:
            `{"kind": value}` if `value` is a `str`; otherwise `value` unchanged.
        """
        if isinstance(value, str):
            return {"kind": value}
        return value


class BackoffKind(StrEnum):
    """The closed set of retry-backoff curves a `RateLimitConfig` may declare.

    A `StrEnum` (matching the `PaginationKind`/`TransformationKind`/`JoinType`/`WriteMode`
    convention elsewhere in the codebase — see `dander.ingestion.pagination.PaginationKind` and
    `dander.writer.base.WriteMode`) so the ingestion runtime gets a named, importable type to
    branch on, while the value still serializes to/from its plain string stably in YAML and JSON.
    An unknown value fails validation automatically rather than silently falling through.

    Attributes:
        FIXED: Constant delay between retries.
        EXPONENTIAL: Exponentially increasing delay between retries (the safer default against a
            throttling API).
    """

    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class IngestionEngine(StrEnum):
    """Execution path used by one source configuration."""

    DLT = "dlt"
    NETSUITE_SUITEQL = "netsuite_suiteql"
    ODOO_JSON2 = "odoo_json2"
    SALESFORCE_BULK2 = "salesforce_bulk2"
    WORKDAY_RAAS = "workday_raas"


class RateLimitConfig(BaseModel):
    """Declarative per-source rate-limit / backoff policy.

    Satisfies the per-source throttling requirement in `steering/02-engineering.md` ("Rate-limit/
    backoff per source (Marketo & Salesforce throttle). Retries are bounded and logged.") by
    giving that requirement a config home. Both the dlt REST adapter and enterprise runtime apply
    this policy; the dlt adapter uses token-bucket pacing and retries only safe read methods.

    Attributes:
        requests_per_second: Steady-state request rate allowed against the source. Must be
            strictly positive (`gt=0`) — a rate of `0` would mean the source can never be read,
            which is never the intent of declaring a policy. Conservative default of `1.0`.
        burst: Maximum number of requests permitted in a short burst above the steady rate.
            `ge=1` (a burst of `0` is nonsensical); default `1` means no burst allowance.
        backoff: Which retry-backoff curve to use when throttled. Defaults to
            `BackoffKind.EXPONENTIAL`, the safer choice against a throttling API.
        max_retries: Bounded retry count. `ge=0` (explicitly allows disabling retries) and
            `le=10` (a sane upper bound so a misconfigured source cannot retry unboundedly);
            default `5`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    requests_per_second: float = Field(default=1.0, gt=0)
    burst: int = Field(default=1, ge=1)
    backoff: BackoffKind = BackoffKind.EXPONENTIAL
    max_retries: int = Field(default=5, ge=0, le=10)


class SourceConfig(BaseModel):
    """Declarative definition of a source system and its endpoints."""

    name: str
    base_url: str
    engine: IngestionEngine = IngestionEngine.DLT
    auth_strategy: str = Field(description="Registered AuthStrategy key, e.g. 'api_key_basic'")
    auth_ref: str | None = Field(
        default=None,
        description="Single secret reference resolved by simple auth strategies (never the value)",
    )
    auth_refs: dict[str, str] = Field(
        default_factory=dict,
        description="Named secret references for multi-credential auth strategies",
    )
    auth_options: dict[str, str | int | bool] = Field(
        default_factory=dict,
        description="Non-secret authentication settings such as an OAuth token URL",
    )
    endpoints: list[Endpoint] = Field(default_factory=list)
    rate_limit: RateLimitConfig | None = Field(
        default=None,
        description="Optional per-source rate-limit/backoff policy; None means unconstrained.",
    )

    @model_validator(mode="after")
    def _validate_auth_configuration(self) -> SourceConfig:
        """Require only the references needed by each built-in authentication strategy."""
        if self.auth_strategy == "none":
            if self.auth_ref is not None or self.auth_refs:
                raise ValueError("Unauthenticated sources cannot declare secret references")
        elif self.auth_strategy == "api_key_basic":
            if not self.auth_ref:
                raise ValueError("api_key_basic requires auth_ref")
        elif self.auth_strategy in {"api_key_bearer", "api_key_basic"}:
            if not self.auth_ref:
                raise ValueError(f"{self.auth_strategy} requires auth_ref")
            if self.auth_refs:
                raise ValueError(f"{self.auth_strategy} cannot declare auth_refs")
        elif self.auth_strategy == "oauth2_client_credentials":
            missing = {"client_id", "client_secret"} - self.auth_refs.keys()
            if missing:
                raise ValueError(
                    "oauth2_client_credentials requires auth_refs for " + ", ".join(sorted(missing))
                )
            token_url = self.auth_options.get("token_url")
            if not isinstance(token_url, str) or not token_url.startswith("https://"):
                raise ValueError(
                    "oauth2_client_credentials requires an HTTPS auth_options.token_url"
                )
            credential_placement = self.auth_options.get("credential_placement", "basic")
            if credential_placement not in {"basic", "body", "query"}:
                raise ValueError(
                    "oauth2_client_credentials auth_options.credential_placement "
                    "must be basic, body, or query"
                )
        elif self.auth_strategy == "oauth2_jwt":
            missing = {"issuer", "private_key"} - self.auth_refs.keys()
            if missing:
                raise ValueError("oauth2_jwt requires auth_refs for " + ", ".join(sorted(missing)))
            token_url = self.auth_options.get("token_url")
            audience = self.auth_options.get("audience")
            scope = self.auth_options.get("scope")
            if not isinstance(token_url, str) or not token_url.startswith("https://"):
                raise ValueError("oauth2_jwt requires an HTTPS auth_options.token_url")
            if scope is not None and (not isinstance(scope, str) or not scope.strip()):
                raise ValueError("oauth2_jwt auth_options.scope must be non-empty when set")
            if audience is not None and (
                not isinstance(audience, str) or not audience.startswith("https://")
            ):
                raise ValueError("oauth2_jwt auth_options.audience must use HTTPS")
            assertion_lifetime = self.auth_options.get("assertion_lifetime", 3600)
            if (
                isinstance(assertion_lifetime, bool)
                or not isinstance(assertion_lifetime, int)
                or not 60 <= assertion_lifetime <= 3600
            ):
                raise ValueError(
                    "oauth2_jwt auth_options.assertion_lifetime must be an integer from 60 to 3600"
                )
            default_expires_in = self.auth_options.get("default_expires_in", 300)
            if (
                isinstance(default_expires_in, bool)
                or not isinstance(default_expires_in, int)
                or not 60 <= default_expires_in <= 3600
            ):
                raise ValueError(
                    "oauth2_jwt auth_options.default_expires_in must be an integer from 60 to 3600"
                )
        elif self.auth_strategy == "oauth1_tba":
            missing = {
                "consumer_key",
                "consumer_secret",
                "token_id",
                "token_secret",
            } - self.auth_refs.keys()
            if missing:
                raise ValueError("oauth1_tba requires auth_refs for " + ", ".join(sorted(missing)))
            account_id = self.auth_options.get("account_id")
            if not isinstance(account_id, str) or not account_id.strip():
                raise ValueError("oauth1_tba requires a non-empty auth_options.account_id")
        return self


class Source(ABC):
    """Extracts records from one source system, endpoint by endpoint."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @abstractmethod
    def discover(self) -> Mapping[str, Any]:
        """Return an inferred schema per endpoint (feeds type casting + the catalog spine)."""

    @abstractmethod
    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        """Yield records for ``endpoint``, optionally bounded by incremental cursor ``since``."""
