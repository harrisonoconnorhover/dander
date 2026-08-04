"""dlt REST adapter tests for DANDER-20."""

from __future__ import annotations

from pathlib import Path
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Any

from dlt.sources.helpers.rest_client.auth import AuthConfigBase
from dlt.sources.helpers.rest_client.paginators import HeaderLinkPaginator
from requests import Request, Response, Session

from dander.ingestion import HeaderCursorPagination, IngestionEngine
from dander.ingestion.config import load_source_config
from dander.ingestion.dlt_backed import (
    DltAuthAdapter,
    DltRestSource,
    UnsupportedRequestBodyError,
)
from dander.ingestion.source import Endpoint, RateLimitConfig, SourceConfig
from dander.security import NoAuth
from dander.security.base import AuthStrategy

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import httpx
    import pytest


class _Auth(AuthStrategy):
    def __init__(self, secrets: _Secrets, auth_ref: str) -> None:
        super().__init__(secrets, auth_ref)
        self.header = f"Basic {token_urlsafe()}"

    def apply(self, request: httpx.Request) -> httpx.Request:
        request.headers["Authorization"] = self.header
        return request


class _Secrets:
    def get_secret(self, reference: str) -> str:
        raise AssertionError(reference)


def _config() -> SourceConfig:
    return SourceConfig(
        name="example",
        base_url="https://example.test/v1",
        auth_strategy="api_key_basic",
        auth_ref="DANDER_TEST_REFERENCE",
        endpoints=[
            Endpoint(
                name="widgets",
                path="/widgets",
                pagination="link_header",
                incremental_cursor="updated_at",
                cursor_param="updated_after",
                primary_key=["id"],
            )
        ],
    )


def test_build_config_maps_auth_pagination_cursor_and_key() -> None:
    auth = _Auth(_Secrets(), "DANDER_TEST_REFERENCE")
    source = DltRestSource(_config(), auth)

    config = source.build_rest_config("widgets", since="2026-01-01T00:00:00Z")

    assert config["client"]["base_url"] == "https://example.test/v1/"
    adapter = config["client"]["auth"]
    assert isinstance(adapter, DltAuthAdapter)
    assert isinstance(adapter, AuthConfigBase)
    prepared = Request("GET", "https://example.test/v1/widgets").prepare()
    assert adapter(prepared).headers["Authorization"] == auth.header
    resource = config["resources"][0]
    assert isinstance(resource, dict)
    assert resource["primary_key"] == ["id"]
    endpoint = resource["endpoint"]
    assert isinstance(endpoint, dict)
    assert isinstance(endpoint["paginator"], HeaderLinkPaginator)
    assert endpoint["params"] == {"updated_after": "2026-01-01T00:00:00Z"}


def test_build_config_supports_public_enveloped_response() -> None:
    config = SourceConfig(
        name="public",
        base_url="https://example.test/v1/boards",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="jobs",
                path="/demo/jobs",
                data_selector="jobs",
                query_params={"include_content": True},
                primary_key=["id"],
            )
        ],
    )

    rest_config = DltRestSource(config, NoAuth()).build_rest_config("jobs")

    resource = rest_config["resources"][0]
    assert isinstance(resource, dict)
    endpoint = resource["endpoint"]
    assert isinstance(endpoint, dict)
    assert endpoint["data_selector"] == "jobs"
    assert endpoint["params"] == {"include_content": True}
    adapter = rest_config["client"]["auth"]
    assert isinstance(adapter, DltAuthAdapter)
    prepared = Request("GET", "https://example.test/v1/boards/demo/jobs").prepare()
    assert "Authorization" not in adapter(prepared).headers


def test_generic_get_adapter_rejects_a_declared_request_body() -> None:
    config = _config()
    config.endpoints[0].request_body = {"q": "SELECT id FROM customer ORDER BY id"}

    try:
        DltRestSource(config, NoAuth()).build_rest_config("widgets")
    except UnsupportedRequestBodyError as error:
        assert "bespoke enterprise ingestion engine" in str(error)
    else:
        raise AssertionError("dlt must not silently ignore a declared request body")


def test_empty_cursor_param_records_watermark_without_sending_filter() -> None:
    config = SourceConfig(
        name="hubspot_test",
        base_url="https://api.hubapi.com",
        auth_strategy="api_key_bearer",
        auth_ref="HUBSPOT_PRIVATE_APP_TOKEN",
        endpoints=[
            Endpoint(
                name="companies",
                path="/crm/v3/objects/companies",
                incremental_cursor="updatedAt",
                cursor_param="",
                primary_key=["id"],
            )
        ],
    )

    rest_config = DltRestSource(config, NoAuth()).build_rest_config(
        "companies", since="2026-01-01T00:00:00Z"
    )
    resource = rest_config["resources"][0]
    assert isinstance(resource, dict)
    endpoint = resource["endpoint"]
    assert isinstance(endpoint, dict)
    assert "params" not in endpoint


def test_marketo_template_maps_provider_auth_pagination_and_rate_limit() -> None:
    connector_path = Path(__file__).parents[2] / "connectors" / "marketo.example.yaml"
    config = load_source_config(connector_path)

    assert config.auth_options["credential_placement"] == "query"
    assert config.rate_limit == RateLimitConfig(
        requests_per_second=5,
        burst=10,
        backoff="exponential",
        max_retries=5,
    )
    rest_config = DltRestSource(
        config,
        _Auth(_Secrets(), "DANDER_TEST_REFERENCE"),
    ).build_rest_config("programs")
    resource = rest_config["resources"][0]
    assert isinstance(resource, dict)
    endpoint = resource["endpoint"]
    assert isinstance(endpoint, dict)
    assert endpoint["data_selector"] == "result"
    assert isinstance(rest_config["client"]["session"], Session)


def test_salesforce_template_maps_bulk2_query_schema_and_jwt_contract() -> None:
    connector_path = Path(__file__).parents[2] / "connectors" / "salesforce_jwt.example.yaml"
    config = load_source_config(connector_path)

    assert config.auth_options == {
        "token_url": "https://login.salesforce.com/services/oauth2/token",
        "audience": "https://login.salesforce.com",
        "subject": "integration-user@example.com",
        "assertion_lifetime": 120,
        "default_expires_in": 300,
    }
    assert config.engine is IngestionEngine.SALESFORCE_BULK2
    endpoint = config.endpoints[0]
    assert endpoint.path == "/jobs/query"
    assert endpoint.incremental_cursor == "SystemModstamp"
    assert isinstance(endpoint.pagination, HeaderCursorPagination)
    assert endpoint.pagination.next_cursor_header == "Sforce-Locator"
    assert endpoint.pagination.page_size == 10_000
    assert endpoint.request_body["operation"] == "queryAll"
    assert str(endpoint.request_body["query"]).endswith("FROM Account")
    assert "ORDER BY" not in str(endpoint.request_body["query"])
    assert {field.name for field in endpoint.raw_schema} >= {
        "attributes",
        "Id",
        "Name",
        "SystemModstamp",
        "IsDeleted",
    }


class _FakeDltSource:
    def with_resources(self, *resource_names: str) -> _FakeDltSource:
        assert resource_names == ("widgets",)
        return self

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        yield {"id": "synthetic"}


def test_extract_yields_mapping_items(monkeypatch: pytest.MonkeyPatch) -> None:
    source = DltRestSource(_config(), _Auth(_Secrets(), "DANDER_TEST_REFERENCE"))

    def fake_rest_api_source(config: object, name: str) -> _FakeDltSource:
        assert config
        assert name == "example"
        return _FakeDltSource()

    monkeypatch.setattr("dander.ingestion.dlt_backed.rest_api_source", fake_rest_api_source)

    assert list(source.extract("widgets")) == [{"id": "synthetic"}]


def test_dlt_session_applies_declared_rate_and_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config.rate_limit = RateLimitConfig(
        requests_per_second=2,
        burst=1,
        backoff="exponential",
        max_retries=2,
    )
    now = [0.0]
    sleeps: list[float] = []
    statuses = iter((429, 503, 200))

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    def fake_send(
        self: Session,
        request: object,
        **kwargs: object,
    ) -> Response:
        del self, request, kwargs
        response = Response()
        response.status_code = next(statuses)
        response._content = b""
        response._content_consumed = True
        return response

    monkeypatch.setattr(Session, "send", fake_send)
    source = DltRestSource(
        config,
        _Auth(_Secrets(), "DANDER_TEST_REFERENCE"),
        sleeper=fake_sleep,
        clock=lambda: now[0],
    )
    rest_config = source.build_rest_config("widgets")
    session = rest_config["client"]["session"]
    assert isinstance(session, Session)

    response = session.send(Request("GET", "https://example.test/widgets").prepare())

    assert response.status_code == 200
    assert sleeps == [0.5, 1.0]


def test_dlt_session_does_not_retry_mutating_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config.rate_limit = RateLimitConfig(max_retries=3)
    calls = 0

    def fake_send(
        self: Session,
        request: object,
        **kwargs: object,
    ) -> Response:
        nonlocal calls
        del self, request, kwargs
        calls += 1
        response = Response()
        response.status_code = 503
        return response

    monkeypatch.setattr(Session, "send", fake_send)
    source = DltRestSource(config, _Auth(_Secrets(), "DANDER_TEST_REFERENCE"))
    rest_config = source.build_rest_config("widgets")
    session = rest_config["client"]["session"]
    assert isinstance(session, Session)

    response = session.send(Request("POST", "https://example.test/widgets").prepare())

    assert response.status_code == 503
    assert calls == 1
