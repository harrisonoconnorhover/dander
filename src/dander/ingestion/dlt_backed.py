"""dlt-backed extraction for declarative REST connectors.

dlt owns request execution, pagination, retry behavior, and response normalization. This adapter
translates Dander's validated `SourceConfig` into dlt's REST configuration while keeping secrets
behind the shared authentication strategy.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import suppress
from threading import Lock
from time import monotonic, sleep
from typing import TYPE_CHECKING, Annotated, Any

import httpx
from dlt.common.configuration.specs.base_configuration import NotResolved, configspec
from dlt.sources.helpers.rest_client.auth import AuthConfigBase
from dlt.sources.helpers.rest_client.paginators import (
    BasePaginator,
    HeaderLinkPaginator,
    JSONLinkPaginator,
    JSONResponseCursorPaginator,
    OffsetPaginator,
    PageNumberPaginator,
    SinglePagePaginator,
)
from dlt.sources.rest_api import rest_api_source
from requests import PreparedRequest, RequestException, Response, Session

from dander.ingestion.pagination import (
    CursorPagination,
    JsonLinkPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
)
from dander.ingestion.source import BackoffKind, Endpoint, RateLimitConfig, Source, SourceConfig
from dander.security.base import AuthStrategy  # noqa: TC001  # configspec resolves at runtime

if TYPE_CHECKING:
    from collections.abc import Callable

    from dlt.sources.rest_api.typing import (
        ClientConfig,
        EndpointResource,
        RESTAPIConfig,
    )
    from dlt.sources.rest_api.typing import Endpoint as DltEndpoint

_LOGGER = logging.getLogger(__name__)
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_METHODS = frozenset({"GET", "HEAD"})


class EndpointNotFoundError(ValueError):
    """Raised when a connector does not declare a requested endpoint."""


class UnsupportedPaginationError(ValueError):
    """Raised when dlt cannot represent a declared pagination variation."""


class UnsupportedRequestBodyError(ValueError):
    """Raised when a POST-style body is assigned to the generic GET adapter."""


class _RateLimitedSession(Session):
    """Apply one source's bounded request-rate and retry contract around dlt HTTP calls."""

    def __init__(
        self,
        policy: RateLimitConfig,
        *,
        source_name: str,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__()
        self._policy = policy
        self._source_name = source_name
        self._sleep = sleeper
        self._clock = clock
        self._tokens = float(policy.burst)
        self._last_refill = clock()
        self._lock = Lock()

    def send(self, request: PreparedRequest, **kwargs: object) -> Response:
        """Send with token-bucket pacing and bounded retries for safe read methods."""
        retryable_method = (request.method or "").upper() in _RETRYABLE_METHODS
        for attempt in range(self._policy.max_retries + 1):
            self._acquire_token()
            try:
                response = super().send(request, **kwargs)
            except RequestException:
                if not retryable_method or attempt == self._policy.max_retries:
                    raise
                self._wait_before_retry(attempt, retry_after=None)
                continue
            if (
                not retryable_method
                or response.status_code not in _RETRYABLE_STATUSES
                or attempt == self._policy.max_retries
            ):
                return response
            retry_after = response.headers.get("Retry-After")
            response.close()
            self._wait_before_retry(attempt, retry_after=retry_after)
        raise AssertionError("bounded dlt retry loop did not return or raise")

    def _acquire_token(self) -> None:
        with self._lock:
            now = self._clock()
            elapsed = max(now - self._last_refill, 0.0)
            self._tokens = min(
                float(self._policy.burst),
                self._tokens + elapsed * self._policy.requests_per_second,
            )
            self._last_refill = now
            if self._tokens < 1.0:
                delay = (1.0 - self._tokens) / self._policy.requests_per_second
                self._sleep(delay)
                now = self._clock()
                elapsed = max(now - self._last_refill, 0.0)
                self._tokens = min(
                    float(self._policy.burst),
                    self._tokens + elapsed * self._policy.requests_per_second,
                )
                self._last_refill = now
            self._tokens = max(self._tokens - 1.0, 0.0)

    def _wait_before_retry(self, attempt: int, *, retry_after: str | None) -> None:
        multiplier = 2**attempt if self._policy.backoff is BackoffKind.EXPONENTIAL else 1
        delay = multiplier / self._policy.requests_per_second
        if retry_after is not None:
            with suppress(ValueError):
                delay = max(delay, min(float(retry_after), 60.0))
        _LOGGER.warning(
            "source_request_retry",
            extra={
                "attempt": attempt + 1,
                "dander_event": "source_request_retry",
                "source": self._source_name,
            },
        )
        self._sleep(delay)


@configspec
class DltAuthAdapter(AuthConfigBase):
    """Apply Dander's provider-neutral authentication strategy to every dlt request."""

    strategy: Annotated[AuthStrategy, NotResolved()] = None  # type: ignore[assignment]

    def __call__(self, request: PreparedRequest) -> PreparedRequest:
        """Copy authentication headers from an equivalent httpx request."""
        if request.url is None:
            raise ValueError("Cannot authenticate a request without a URL")
        candidate = httpx.Request(
            request.method or "GET",
            request.url,
            headers={str(key): str(value) for key, value in request.headers.items()},
        )
        authenticated = self.strategy.apply(candidate)
        request.headers.update(dict(authenticated.headers))
        return request


class DltRestSource(Source):
    """Adapt a standard REST connector to Dander's `Source` contract using dlt."""

    def __init__(
        self,
        config: SourceConfig,
        auth: AuthStrategy,
        *,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(config)
        self._auth = auth
        self._session = (
            _RateLimitedSession(
                config.rate_limit,
                source_name=config.name,
                sleeper=sleeper,
                clock=clock,
            )
            if config.rate_limit is not None
            else None
        )

    def discover(self) -> Mapping[str, Any]:
        """Return validated endpoint metadata without fetching source row values.

        Runtime schema inference remains dlt's responsibility during extraction. Returning only
        declarations here avoids persisting or logging sampled sensitive values.
        """
        return {
            endpoint.name: {
                "path": endpoint.path,
                "primary_key": list(endpoint.primary_key),
                "incremental_cursor": endpoint.incremental_cursor,
            }
            for endpoint in self.config.endpoints
        }

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        """Yield normalized records for one configured endpoint."""
        rest_config = self.build_rest_config(endpoint, since=since)
        source = rest_api_source(rest_config, name=self.config.name).with_resources(endpoint)
        for item in source:
            if isinstance(item, Mapping):
                yield item
            else:
                raise TypeError(
                    f"Endpoint {endpoint!r} produced a non-mapping item of type "
                    f"{type(item).__name__}"
                )

    def build_rest_config(self, endpoint_name: str, *, since: str | None = None) -> RESTAPIConfig:
        """Translate one Dander endpoint into a dlt REST API configuration.

        This pure configuration boundary is public so callers can inspect a credential-free plan
        before execution. The returned object contains an authentication adapter for private
        sources and must never be logged.
        """
        endpoint = self._get_endpoint(endpoint_name)
        if endpoint.request_body:
            raise UnsupportedRequestBodyError(
                f"Endpoint {endpoint_name!r} declares request_body, which requires a bespoke "
                "enterprise ingestion engine"
            )
        params: dict[str, Any] = dict(endpoint.query_params)
        paginator = self._build_paginator(endpoint, params)
        # An explicit empty cursor_param is a deliberate read-only watermark: retain the
        # committed cursor for restart evidence, but do not send it to an endpoint that cannot
        # filter by modification time. None preserves the legacy fallback to the cursor field.
        cursor_param = (
            endpoint.incremental_cursor if endpoint.cursor_param is None else endpoint.cursor_param
        )
        if since is not None and cursor_param:
            params[cursor_param] = since

        dlt_endpoint: DltEndpoint = {
            "path": endpoint.path.lstrip("/"),
            "paginator": paginator,
        }
        if endpoint.data_selector is not None:
            dlt_endpoint["data_selector"] = endpoint.data_selector
        if params:
            dlt_endpoint["params"] = params

        resource: EndpointResource = {
            "name": endpoint.name,
            "endpoint": dlt_endpoint,
        }
        if endpoint.primary_key:
            resource["primary_key"] = endpoint.primary_key

        client: ClientConfig = {
            "base_url": f"{self.config.base_url.rstrip('/')}/",
            "auth": DltAuthAdapter(self._auth),
        }
        if self._session is not None:
            client["session"] = self._session
        return {
            "client": client,
            "resources": [resource],
        }

    def _get_endpoint(self, endpoint_name: str) -> Endpoint:
        for endpoint in self.config.endpoints:
            if endpoint.name == endpoint_name:
                return endpoint
        raise EndpointNotFoundError(
            f"Connector {self.config.name!r} has no endpoint {endpoint_name!r}"
        )

    @staticmethod
    def _build_paginator(endpoint: Endpoint, params: dict[str, Any]) -> BasePaginator:
        pagination = endpoint.pagination
        if isinstance(pagination, NoPagination):
            return SinglePagePaginator()
        if isinstance(pagination, LinkHeaderPagination):
            if pagination.header_name.lower() != "link":
                raise UnsupportedPaginationError(
                    "dlt link-header pagination requires the standard Link header"
                )
            return HeaderLinkPaginator(links_next_key=pagination.rel)
        if isinstance(pagination, JsonLinkPagination):
            return JSONLinkPaginator(next_url_path=pagination.next_url_path)
        if isinstance(pagination, OffsetPagination):
            return OffsetPaginator(
                limit=pagination.page_size,
                offset_param=pagination.offset_param,
                limit_param=pagination.limit_param,
                total_path=None,
            )
        if isinstance(pagination, PageNumberPagination):
            params[pagination.size_param] = pagination.page_size
            return PageNumberPaginator(
                base_page=pagination.start_page,
                page_param=pagination.page_param,
                total_path=None,
            )
        if isinstance(pagination, CursorPagination):
            if pagination.size_param and pagination.page_size is not None:
                params[pagination.size_param] = pagination.page_size
            return JSONResponseCursorPaginator(
                cursor_path=pagination.next_cursor_path,
                cursor_param=pagination.cursor_param,
                stop_after_empty_page=True,
            )
        raise AssertionError(f"Unhandled pagination strategy: {type(pagination).__name__}")
