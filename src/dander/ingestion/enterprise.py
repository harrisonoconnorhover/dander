"""Hand-rolled extraction for enterprise APIs whose contracts exceed dlt's generic path."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx

from dander.ingestion.pagination import NoPagination, OffsetPagination, PageNumberPagination
from dander.ingestion.source import BackoffKind, Endpoint, Source

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from dander.ingestion.source import SourceConfig
    from dander.security.base import AuthStrategy


class EnterpriseSourceError(ValueError):
    """Raised when an enterprise response cannot satisfy its declared contract."""


class _Response(Protocol):
    def raise_for_status(self) -> object:
        """Raise for an unsuccessful response."""

    def json(self) -> object:
        """Decode and return the JSON response."""


class EnterpriseHttpClient(Protocol):
    """Injectable HTTP transport used by hand-rolled enterprise sources."""

    def send(self, request: httpx.Request) -> _Response:
        """Send one fully authenticated request."""


class EnterpriseSource(Source):
    """Shared authenticated transport for bespoke enterprise sources."""

    def __init__(
        self,
        config: SourceConfig,
        auth: AuthStrategy,
        *,
        client: EnterpriseHttpClient | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        super().__init__(config)
        self._auth = auth
        self._client = client or cast("EnterpriseHttpClient", httpx.Client())
        self._sleep = sleeper

    def _endpoint(self, name: str) -> Endpoint:
        for endpoint in self.config.endpoints:
            if endpoint.name == name:
                return endpoint
        raise EnterpriseSourceError(f"Connector {self.config.name!r} has no endpoint {name!r}")

    def _send(self, request: httpx.Request, endpoint: str) -> _Response:
        policy = self.config.rate_limit
        max_retries = policy.max_retries if policy is not None else 0
        for attempt in range(max_retries + 1):
            try:
                # Reapply authentication for every attempt. This is required for OAuth1 because
                # a retry must receive a fresh nonce/signature rather than replaying a rejected
                # Authorization header.
                response = self._client.send(self._auth.apply(request))
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if 400 <= status < 500 and status != 429:
                    if status == 401:
                        reason = "authentication failed"
                    elif status == 403:
                        reason = "permission denied"
                    else:
                        reason = "request was rejected"
                    raise EnterpriseSourceError(
                        f"Endpoint {endpoint!r} {reason} (HTTP {status})"
                    ) from error
                retry_error: httpx.HTTPError = error
            except httpx.HTTPError as error:
                retry_error = error
            if attempt == max_retries:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint!r} request failed after bounded retries"
                ) from retry_error
            assert policy is not None
            multiplier = 2**attempt if policy.backoff is BackoffKind.EXPONENTIAL else 1
            self._sleep(multiplier / policy.requests_per_second)
        raise AssertionError("bounded retry loop did not return or raise")


class WorkdayRaasSource(EnterpriseSource):
    """Extract Workday RaaS JSON reports with explicit paging and type overrides."""

    def __init__(
        self,
        config: SourceConfig,
        auth: AuthStrategy,
        *,
        client: EnterpriseHttpClient | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        super().__init__(config, auth, client=client, sleeper=sleeper)

    def discover(self) -> Mapping[str, Any]:
        """Return declared report schemas without sampling employee data."""
        return {
            endpoint.name: {
                "path": endpoint.path,
                "primary_key": list(endpoint.primary_key),
                "incremental_cursor": endpoint.incremental_cursor,
                "field_types": dict(endpoint.field_types),
            }
            for endpoint in self.config.endpoints
        }

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        """Yield one validated, cast report row at a time."""
        declaration = self._endpoint(endpoint)
        pagination = declaration.pagination
        if not isinstance(pagination, (NoPagination, PageNumberPagination)):
            raise EnterpriseSourceError(
                f"Workday RaaS endpoint {endpoint!r} requires none or page_number pagination"
            )

        page = pagination.start_page if isinstance(pagination, PageNumberPagination) else 0
        while True:
            params: dict[str, str | int] = {"format": "json"}
            cursor_param = declaration.cursor_param or declaration.incremental_cursor
            if since is not None and cursor_param is not None:
                params[cursor_param] = since
            if isinstance(pagination, PageNumberPagination):
                params[pagination.page_param] = page
                params[pagination.size_param] = pagination.page_size

            request = httpx.Request(
                "GET",
                f"{self.config.base_url.rstrip('/')}/{declaration.path.lstrip('/')}",
                params=params,
            )
            response = self._send(request, endpoint)
            rows = _select_rows(response.json(), declaration)
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise EnterpriseSourceError(
                        f"Endpoint {endpoint!r} returned a non-mapping row at index {index}"
                    )
                yield _cast_row(row, declaration)

            if not isinstance(pagination, PageNumberPagination):
                return
            if len(rows) < pagination.page_size:
                return
            if self.config.rate_limit is not None:
                self._sleep(1 / self.config.rate_limit.requests_per_second)
            page += 1


class NetSuiteSuiteQLSource(EnterpriseSource):
    """Execute one declared, stably ordered SuiteQL query with bounded offset paging."""

    def discover(self) -> Mapping[str, Any]:
        """Return the declared query contract without contacting NetSuite."""
        return {
            endpoint.name: {
                "path": endpoint.path,
                "primary_key": list(endpoint.primary_key),
                "incremental_cursor": endpoint.incremental_cursor,
                "raw_schema": [field.model_dump(by_alias=True) for field in endpoint.raw_schema],
            }
            for endpoint in self.config.endpoints
        }

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        """Yield SuiteQL items while validating NetSuite's page metadata."""
        del since  # The first slice records a watermark but deliberately performs a full read.
        declaration = self._endpoint(endpoint)
        pagination = declaration.pagination
        if not isinstance(pagination, OffsetPagination):
            raise EnterpriseSourceError(
                f"NetSuite SuiteQL endpoint {endpoint!r} requires offset pagination"
            )
        query = declaration.request_body.get("q")
        if not isinstance(query, str) or not query.strip():
            raise EnterpriseSourceError(
                f"NetSuite SuiteQL endpoint {endpoint!r} requires request_body.q"
            )

        offset = 0
        while True:
            request = httpx.Request(
                "POST",
                f"{self.config.base_url.rstrip('/')}/{declaration.path.lstrip('/')}",
                params={
                    pagination.limit_param: pagination.page_size,
                    pagination.offset_param: offset,
                },
                headers={"Content-Type": "application/json", "Prefer": "transient"},
                json={"q": query},
            )
            response = self._send(request, endpoint)
            rows, has_more = _suiteql_page(response.json(), declaration, offset)
            for row in rows:
                # NetSuite adds HATEOAS links to every SuiteQL item even when they were not
                # selected. They are transport metadata, not part of the declared raw relation.
                yield _cast_row(
                    {key: value for key, value in row.items() if key != "links"}, declaration
                )
            if not has_more:
                return
            if not rows:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint!r} returned hasMore=true with an empty page"
                )
            offset += len(rows)
            if self.config.rate_limit is not None:
                self._sleep(1 / self.config.rate_limit.requests_per_second)


def _suiteql_page(
    payload: object,
    endpoint: Endpoint,
    expected_offset: int,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(payload, dict):
        raise EnterpriseSourceError(f"Endpoint {endpoint.name!r} response must be an object")
    items = payload.get("items")
    count = payload.get("count")
    offset = payload.get("offset")
    total_results = payload.get("totalResults")
    has_more = payload.get("hasMore")
    if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
        raise EnterpriseSourceError(f"Endpoint {endpoint.name!r} items must be mapping records")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or isinstance(total_results, bool)
        or not isinstance(total_results, int)
        or not isinstance(has_more, bool)
    ):
        raise EnterpriseSourceError(
            f"Endpoint {endpoint.name!r} response has invalid SuiteQL page metadata"
        )
    expected_has_more = offset + count < total_results
    if (
        count != len(items)
        or offset != expected_offset
        or total_results < offset + count
        or has_more is not expected_has_more
    ):
        raise EnterpriseSourceError(
            f"Endpoint {endpoint.name!r} response has inconsistent SuiteQL page metadata"
        )
    return [dict(row) for row in cast("list[dict[str, Any]]", items)], has_more


def _select_rows(payload: object, endpoint: Endpoint) -> list[object]:
    selected = payload
    if endpoint.data_selector is not None:
        for part in endpoint.data_selector.split("."):
            if not isinstance(selected, dict) or part not in selected:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint.name!r} response is missing its data selector"
                )
            selected = selected[part]
    if not isinstance(selected, list):
        raise EnterpriseSourceError(f"Endpoint {endpoint.name!r} response data must be a list")
    return selected


def _cast_row(row: dict[str, Any], endpoint: Endpoint) -> dict[str, Any]:
    cast = dict(row)
    for field, data_type in endpoint.field_types.items():
        if field not in cast or cast[field] is None:
            continue
        try:
            cast[field] = _cast_value(cast[field], data_type)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise EnterpriseSourceError(
                f"Endpoint {endpoint.name!r} field {field!r} cannot be cast to {data_type}"
            ) from error
    return cast


def _cast_value(value: object, data_type: str) -> object:
    if data_type == "STRING":
        return str(value)
    if data_type == "INT64":
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise TypeError
        return int(value)
    if data_type == "FLOAT64":
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise TypeError
        return float(value)
    if data_type == "NUMERIC":
        if isinstance(value, bool):
            raise TypeError
        return Decimal(str(value))
    if data_type == "BOOL":
        if isinstance(value, bool):
            return value
        normalized = str(value).lower()
        if normalized not in {"true", "false"}:
            raise ValueError
        return normalized == "true"
    if data_type == "DATE":
        return date.fromisoformat(str(value))
    if data_type == "TIMESTAMP":
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    raise AssertionError(f"Unhandled declared data type: {data_type}")
