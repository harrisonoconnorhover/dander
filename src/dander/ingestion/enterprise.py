"""Hand-rolled extraction for enterprise APIs whose contracts exceed dlt's generic path."""

from __future__ import annotations

import csv
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx

from dander.ingestion.pagination import (
    HeaderCursorPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
)
from dander.ingestion.source import BackoffKind, Endpoint, RawField, Source

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from dander.ingestion.source import SourceConfig
    from dander.security.base import AuthStrategy

_LOGGER = logging.getLogger(__name__)
_SALESFORCE_ACTIVE_STATES = frozenset({"UploadComplete", "InProgress"})
_SALESFORCE_FAILED_STATES = frozenset({"Aborted", "Failed"})
_SALESFORCE_MAX_POLLS = 240
_SOQL_SCALE_BREAKERS = re.compile(r"\b(?:GROUP\s+BY|LIMIT|OFFSET|ORDER\s+BY|TYPEOF|WHERE)\b", re.I)


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


class _StreamingResponse(Protocol):
    headers: Mapping[str, str]

    def raise_for_status(self) -> object:
        """Raise for an unsuccessful response."""

    def iter_lines(self) -> Iterator[str]:
        """Yield decoded response lines without materializing the complete body."""

    def close(self) -> None:
        """Release the streaming connection."""


class _StreamingHttpClient(Protocol):
    def send(self, request: httpx.Request, *, stream: bool) -> _StreamingResponse:
        """Send one authenticated request with response streaming enabled."""


class EnterpriseSource(Source):
    """Shared authenticated transport for sources that fully control the request cycle."""

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


class OdooJson2Source(EnterpriseSource):
    """Read Odoo 19+ models through the JSON-2 ``search_read`` API."""

    def discover(self) -> Mapping[str, Any]:
        """Return declared model schemas without reading business records."""
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
        """Yield one normalized Odoo record at a time using bounded offset pages."""
        declaration = self._endpoint(endpoint)
        pagination = _validate_odoo_endpoint(declaration)
        domain = _odoo_domain(declaration, since)

        offset = 0
        while True:
            request = _build_odoo_request(
                self.config,
                declaration,
                pagination,
                domain=domain,
                offset=offset,
            )
            response = self._send(request, endpoint)
            rows = _select_odoo_rows(response.json(), endpoint)
            for row in rows:
                normalized = _normalize_odoo_row(row, declaration.raw_schema)
                yield _cast_row(normalized, declaration)

            if len(rows) < pagination.page_size:
                return
            if self.config.rate_limit is not None:
                self._sleep(1 / self.config.rate_limit.requests_per_second)
            offset += pagination.page_size


class SalesforceBulk2Source(EnterpriseSource):
    """Run bounded, server-filtered Salesforce Bulk API 2.0 query jobs."""

    def discover(self) -> Mapping[str, Any]:
        """Return declared query schemas without contacting Salesforce."""
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
        """Create one query job, stream every result page, and delete the completed job."""
        declaration = self._endpoint(endpoint)
        pagination = _validate_salesforce_endpoint(declaration)
        body = _salesforce_query_body(declaration, since)
        job_url = f"{self.config.base_url.rstrip('/')}/{declaration.path.lstrip('/')}"
        response = self._send(
            httpx.Request(
                "POST",
                job_url,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=body,
            ),
            endpoint,
        )
        job_id = _salesforce_job_id(response.json(), endpoint)
        try:
            self._await_salesforce_job(job_url, job_id, endpoint)
            yield from self._salesforce_results(
                job_url,
                job_id,
                endpoint,
                declaration,
                pagination,
            )
        finally:
            self._delete_salesforce_job(job_url, job_id, endpoint)

    def _await_salesforce_job(self, job_url: str, job_id: str, endpoint: str) -> None:
        poll_url = f"{job_url}/{job_id}"
        for attempt in range(_SALESFORCE_MAX_POLLS):
            response = self._send(
                httpx.Request("GET", poll_url, headers={"Accept": "application/json"}),
                endpoint,
            )
            payload = _salesforce_job_payload(response.json(), endpoint)
            state = payload.get("state")
            if state == "JobComplete":
                return
            if state in _SALESFORCE_FAILED_STATES:
                detail = payload.get("errorMessage")
                suffix = f": {detail}" if isinstance(detail, str) and detail else ""
                raise EnterpriseSourceError(
                    f"Salesforce Bulk API job for endpoint {endpoint!r} ended in {state}{suffix}"
                )
            if state not in _SALESFORCE_ACTIVE_STATES:
                raise EnterpriseSourceError(
                    f"Salesforce Bulk API job for endpoint {endpoint!r} returned "
                    f"unknown state {state!r}"
                )
            if attempt + 1 < _SALESFORCE_MAX_POLLS:
                self._sleep(self._salesforce_poll_delay())
        raise EnterpriseSourceError(
            f"Salesforce Bulk API job for endpoint {endpoint!r} did not finish within "
            f"{_SALESFORCE_MAX_POLLS} polls"
        )

    def _salesforce_results(
        self,
        job_url: str,
        job_id: str,
        endpoint: str,
        declaration: Endpoint,
        pagination: HeaderCursorPagination,
    ) -> Iterator[Mapping[str, Any]]:
        results_url = f"{job_url}/{job_id}/results"
        locator: str | None = None
        seen_locators: set[str] = set()
        while True:
            params: dict[str, str | int] = {pagination.size_param: pagination.page_size}
            if locator is not None:
                params[pagination.cursor_param] = locator
            response = self._send_streaming(
                httpx.Request(
                    "GET",
                    results_url,
                    params=params,
                    headers={"Accept": "text/csv", "Accept-Encoding": "gzip"},
                ),
                endpoint,
            )
            try:
                rows = csv.DictReader(response.iter_lines())
                fieldnames = list(rows.fieldnames or ())
                if fieldnames:
                    fieldnames[0] = fieldnames[0].removeprefix("\ufeff")
                    rows.fieldnames = fieldnames
                _validate_salesforce_csv_fields(fieldnames, declaration)
                page_rows = 0
                for index, row in enumerate(rows):
                    if None in row or any(value is None for value in row.values()):
                        raise EnterpriseSourceError(
                            f"Endpoint {endpoint!r} returned malformed CSV row {index}"
                        )
                    page_rows += 1
                    yield _normalize_salesforce_csv_row(row, declaration, index=index)
                _validate_salesforce_page_count(response.headers, page_rows, endpoint)
                next_locator = response.headers.get(pagination.next_cursor_header)
            finally:
                response.close()

            if next_locator is None:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint!r} response omitted {pagination.next_cursor_header!r}"
                )
            if next_locator == pagination.terminal_value:
                return
            if next_locator in seen_locators:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint!r} repeated a Salesforce result locator"
                )
            seen_locators.add(next_locator)
            locator = next_locator

    def _send_streaming(self, request: httpx.Request, endpoint: str) -> _StreamingResponse:
        policy = self.config.rate_limit
        max_retries = policy.max_retries if policy is not None else 0
        client = cast("_StreamingHttpClient", self._client)
        for attempt in range(max_retries + 1):
            response: _StreamingResponse | None = None
            try:
                response = client.send(self._auth.apply(request), stream=True)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                if response is not None:
                    response.close()
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
                if response is not None:
                    response.close()
                retry_error = error
            if attempt == max_retries:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint!r} request failed after bounded retries"
                ) from retry_error
            assert policy is not None
            multiplier = 2**attempt if policy.backoff is BackoffKind.EXPONENTIAL else 1
            self._sleep(multiplier / policy.requests_per_second)
        raise AssertionError("bounded streaming retry loop did not return or raise")

    def _delete_salesforce_job(self, job_url: str, job_id: str, endpoint: str) -> None:
        try:
            self._send(httpx.Request("DELETE", f"{job_url}/{job_id}"), endpoint)
        except EnterpriseSourceError:
            _LOGGER.warning(
                "salesforce_query_job_cleanup_failed",
                extra={"dander_event": "salesforce_query_job_cleanup_failed", "endpoint": endpoint},
            )

    def _salesforce_poll_delay(self) -> float:
        if self.config.rate_limit is None:
            return 1.0
        return max(1.0, 1 / self.config.rate_limit.requests_per_second)


def _validate_salesforce_endpoint(endpoint: Endpoint) -> HeaderCursorPagination:
    pagination = endpoint.pagination
    if not isinstance(pagination, HeaderCursorPagination):
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} requires header_cursor pagination"
        )
    if endpoint.path.rstrip("/") != "/jobs/query":
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} must target /jobs/query"
        )
    if not endpoint.raw_schema:
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} requires a declared raw schema"
        )
    query = endpoint.request_body.get("query")
    operation = endpoint.request_body.get("operation")
    if not isinstance(query, str) or not query.strip():
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} requires request_body.query"
        )
    if operation not in {"query", "queryAll"}:
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} requires query or queryAll operation"
        )
    if ";" in query or _SOQL_SCALE_BREAKERS.search(query):
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint.name!r} query must be one unfiltered, "
            "unordered SELECT; Dander adds the watermark filter and preserves PK chunking"
        )
    return pagination


def _salesforce_query_body(endpoint: Endpoint, since: str | None) -> dict[str, object]:
    body: dict[str, object] = dict(endpoint.request_body)
    query = cast("str", body["query"]).strip()
    if since is not None:
        if endpoint.incremental_cursor is None:
            raise EnterpriseSourceError(
                f"Endpoint {endpoint.name!r} received a cursor without incremental_cursor"
            )
        query = (
            f"{query} WHERE {endpoint.incremental_cursor} >= "
            f"{_salesforce_datetime_literal(since, endpoint.name)}"
        )
    body["query"] = query
    body.setdefault("contentType", "CSV")
    body.setdefault("columnDelimiter", "COMMA")
    body.setdefault("lineEnding", "LF")
    return body


def _salesforce_datetime_literal(value: str, endpoint: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise EnterpriseSourceError(
            f"Endpoint {endpoint!r} received an invalid Salesforce timestamp cursor"
        ) from error
    if parsed.tzinfo is None:
        raise EnterpriseSourceError(
            f"Endpoint {endpoint!r} received a timestamp cursor without a timezone"
        )
    utc = parsed.astimezone(UTC)
    milliseconds = utc.microsecond // 1000
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"


def _salesforce_job_payload(payload: object, endpoint: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint!r} returned invalid job metadata"
        )
    return payload


def _salesforce_job_id(payload: object, endpoint: str) -> str:
    job_id = _salesforce_job_payload(payload, endpoint).get("id")
    if not isinstance(job_id, str) or not job_id:
        raise EnterpriseSourceError(
            f"Salesforce Bulk API endpoint {endpoint!r} omitted its query job id"
        )
    return job_id


def _validate_salesforce_csv_fields(fieldnames: list[str], endpoint: Endpoint) -> None:
    if (
        not fieldnames
        or any(not name for name in fieldnames)
        or len(fieldnames) != len(set(fieldnames))
    ):
        raise EnterpriseSourceError(
            f"Endpoint {endpoint.name!r} returned invalid Salesforce CSV headers"
        )
    declared = {field.name for field in endpoint.raw_schema}
    if unknown := sorted(set(fieldnames) - declared):
        raise EnterpriseSourceError(
            f"Endpoint {endpoint.name!r} returned undeclared Salesforce field {unknown[0]!r}"
        )
    required = {field.name for field in endpoint.raw_schema if field.mode == "REQUIRED"}
    if missing := sorted(required - set(fieldnames)):
        raise EnterpriseSourceError(
            f"Endpoint {endpoint.name!r} omitted required Salesforce field {missing[0]!r}"
        )


def _normalize_salesforce_csv_row(
    row: Mapping[str | None, str | None], endpoint: Endpoint, *, index: int
) -> dict[str, object | None]:
    fields = {field.name: field for field in endpoint.raw_schema}
    normalized: dict[str, object | None] = {}
    for raw_name, raw_value in row.items():
        assert raw_name is not None and raw_value is not None
        if raw_value == "":
            normalized[raw_name] = None
            continue
        field = fields[raw_name]
        if field.data_type == "BOOL":
            boolean = raw_value.strip().lower()
            if boolean not in {"true", "false"}:
                raise EnterpriseSourceError(
                    f"Endpoint {endpoint.name!r} returned invalid BOOL CSV field "
                    f"{raw_name!r} at row {index}"
                )
            normalized[raw_name] = boolean == "true"
        else:
            normalized[raw_name] = raw_value
    return normalized


def _validate_salesforce_page_count(headers: Mapping[str, str], actual: int, endpoint: str) -> None:
    raw_count = headers.get("Sforce-NumberOfRecords")
    try:
        expected = int(raw_count) if raw_count is not None else None
    except ValueError as error:
        raise EnterpriseSourceError(
            f"Endpoint {endpoint!r} returned an invalid Salesforce page count"
        ) from error
    if expected is None or expected != actual:
        raise EnterpriseSourceError(
            f"Endpoint {endpoint!r} Salesforce page count did not match its CSV rows"
        )


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


def _validate_odoo_endpoint(endpoint: Endpoint) -> OffsetPagination:
    pagination = endpoint.pagination
    if not isinstance(pagination, OffsetPagination):
        raise EnterpriseSourceError(
            f"Odoo JSON-2 endpoint {endpoint.name!r} requires offset pagination"
        )
    if not endpoint.raw_schema:
        raise EnterpriseSourceError(
            f"Odoo JSON-2 endpoint {endpoint.name!r} requires a declared raw schema"
        )
    if not endpoint.path.startswith("/json/2/") or not endpoint.path.endswith("/search_read"):
        raise EnterpriseSourceError(
            f"Odoo JSON-2 endpoint {endpoint.name!r} must target /json/2/<model>/search_read"
        )
    return pagination


def _odoo_domain(endpoint: Endpoint, since: str | None) -> list[list[object]]:
    if since is None or endpoint.incremental_cursor is None:
        return []
    # The inclusive boundary deliberately replays tied watermark values; SCD1 publication makes
    # that replay idempotent and avoids dropping rows that share a timestamp.
    return [[endpoint.incremental_cursor, ">=", since]]


def _build_odoo_request(
    config: SourceConfig,
    endpoint: Endpoint,
    pagination: OffsetPagination,
    *,
    domain: list[list[object]],
    offset: int,
) -> httpx.Request:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    database = config.auth_options.get("database")
    if database is not None:
        if not isinstance(database, str) or not database.strip():
            raise EnterpriseSourceError(
                "Odoo auth_options.database must be a non-empty string when set"
            )
        headers["X-Odoo-Database"] = database
    body: dict[str, object] = {
        "domain": domain,
        "fields": [field.name for field in endpoint.raw_schema],
        "limit": pagination.page_size,
        "offset": offset,
        "order": "id asc",
    }
    return httpx.Request(
        "POST",
        f"{config.base_url.rstrip('/')}/{endpoint.path.lstrip('/')}",
        headers=headers,
        json=body,
    )


def _select_odoo_rows(payload: object, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise EnterpriseSourceError(f"Endpoint {endpoint!r} response data must be a list")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise EnterpriseSourceError(
                f"Endpoint {endpoint!r} returned a non-mapping row at index {index}"
            )
        rows.append(row)
    return rows


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


def _normalize_odoo_row(row: dict[str, Any], schema: list[RawField]) -> dict[str, Any]:
    """Convert Odoo's ``false`` sentinel to null for non-boolean scalar fields."""
    normalized = dict(row)
    for field in schema:
        if normalized.get(field.name) is False and field.data_type != "BOOL":
            normalized[field.name] = None
    return normalized


def _cast_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TypeError
    return int(value)


def _cast_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TypeError
    return float(value)


def _cast_numeric(value: object) -> Decimal:
    if isinstance(value, bool):
        raise TypeError
    return Decimal(str(value))


def _cast_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).lower()
    if normalized not in {"true", "false"}:
        raise ValueError
    return normalized == "true"


def _cast_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


_SCALAR_CASTERS: dict[str, Callable[[object], object]] = {
    "BOOL": _cast_boolean,
    "DATE": lambda value: date.fromisoformat(str(value)),
    "FLOAT64": _cast_float,
    "INT64": _cast_integer,
    "NUMERIC": _cast_numeric,
    "STRING": str,
    "TIMESTAMP": _cast_timestamp,
}


def _cast_value(value: object, data_type: str) -> object:
    try:
        caster = _SCALAR_CASTERS[data_type]
    except KeyError as error:
        raise AssertionError(f"Unhandled declared data type: {data_type}") from error
    return caster(value)
