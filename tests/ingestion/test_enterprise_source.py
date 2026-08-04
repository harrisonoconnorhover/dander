"""Concrete hand-rolled enterprise-source tests for DANDER-32."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from dander.ingestion import (
    CursorPagination,
    EnterpriseSource,
    EnterpriseSourceError,
    OdooJson2Source,
    SalesforceBulk2Source,
    WorkdayRaasSource,
    load_source_config,
)
from dander.ingestion.source import Endpoint, RateLimitConfig, SourceConfig
from dander.security.base import AuthStrategy

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Auth(AuthStrategy):
    def __init__(self) -> None:
        self.requests = 0

    def apply(self, request: httpx.Request) -> httpx.Request:
        self.requests += 1
        request.headers["Authorization"] = "Basic synthetic"
        return request


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        if isinstance(self._payload, httpx.HTTPError):
            raise self._payload
        return None

    def json(self) -> object:
        return self._payload


class _CsvResponse(_Response):
    def __init__(self, lines: list[str], *, locator: str, records: int) -> None:
        super().__init__(None)
        self._lines = lines
        self.headers = httpx.Headers(
            {
                "Sforce-Locator": locator,
                "Sforce-NumberOfRecords": str(records),
            }
        )
        self.closed = False

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = iter(payloads)
        self.requests: list[httpx.Request] = []
        self.streams: list[bool] = []

    def send(self, request: httpx.Request, *, stream: bool = False) -> _Response:
        self.requests.append(request)
        self.streams.append(stream)
        payload = next(self._payloads)
        return payload if isinstance(payload, _Response) else _Response(payload)


def _config(*, page_size: int = 2) -> SourceConfig:
    return SourceConfig(
        name="workday",
        base_url="https://workday.example.test/ccx/service/customreport2/tenant",
        auth_strategy="api_key_basic",
        auth_ref="WORKDAY_BASIC",
        endpoints=[
            Endpoint(
                name="workers",
                path="owner/report",
                pagination={
                    "kind": "page_number",
                    "page_param": "page",
                    "size_param": "count",
                    "page_size": page_size,
                    "start_page": 1,
                },
                data_selector="report.entries",
                incremental_cursor="updated_at",
                cursor_param="updated_after",
                primary_key=["worker_id"],
                field_types={
                    "worker_id": "STRING",
                    "active": "BOOL",
                    "salary": "NUMERIC",
                    "start_date": "DATE",
                    "updated_at": "TIMESTAMP",
                },
            )
        ],
    )


def _odoo_config(*, page_size: int = 2) -> SourceConfig:
    return SourceConfig(
        name="odoo",
        engine="odoo_json2",
        base_url="https://odoo.example.test",
        auth_strategy="api_key_bearer",
        auth_ref="ODOO_API_KEY",
        auth_options={"database": "dander_test"},
        endpoints=[
            Endpoint(
                name="partners",
                path="/json/2/res.partner/search_read",
                pagination={"kind": "offset", "page_size": page_size},
                incremental_cursor="write_date",
                primary_key=["id"],
                field_types={"id": "INT64", "active": "BOOL", "is_company": "BOOL"},
                raw_schema=[
                    {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                    {"name": "name", "type": "STRING"},
                    {"name": "email", "type": "STRING"},
                    {"name": "active", "type": "BOOL"},
                    {"name": "is_company", "type": "BOOL"},
                    {"name": "write_date", "type": "STRING"},
                ],
            )
        ],
    )


def _salesforce_config(*, page_size: int = 2) -> SourceConfig:
    config = load_source_config(
        Path(__file__).parents[2] / "connectors" / "salesforce_jwt.example.yaml"
    )
    config.base_url = "https://salesforce.example.test/services/data/v67.0"
    pagination = config.endpoints[0].pagination
    config.endpoints[0].pagination = pagination.model_copy(update={"page_size": page_size})
    return config


def test_workday_source_pages_authenticates_casts_and_passes_cursor() -> None:
    client = _Client(
        [
            {
                "report": {
                    "entries": [
                        {
                            "worker_id": 101,
                            "active": "true",
                            "salary": "125.50",
                            "start_date": "2026-01-02",
                            "updated_at": "2026-07-29T12:00:00Z",
                        },
                        {
                            "worker_id": 102,
                            "active": False,
                            "salary": 80,
                            "start_date": "2025-12-31",
                            "updated_at": "2026-07-29T13:00:00+00:00",
                        },
                    ]
                }
            },
            {"report": {"entries": []}},
        ]
    )
    auth = _Auth()
    source = WorkdayRaasSource(_config(), auth, client=client)

    rows = list(source.extract("workers", since="2026-07-28T00:00:00Z"))

    assert isinstance(source, EnterpriseSource)
    assert rows[0] == {
        "worker_id": "101",
        "active": True,
        "salary": Decimal("125.50"),
        "start_date": date(2026, 1, 2),
        "updated_at": datetime(2026, 7, 29, 12, tzinfo=UTC),
    }
    assert rows[1]["active"] is False
    assert auth.requests == 2
    assert [request.url.params["page"] for request in client.requests] == ["1", "2"]
    assert all(request.url.params["count"] == "2" for request in client.requests)
    assert all(
        request.url.params["updated_after"] == "2026-07-28T00:00:00Z" for request in client.requests
    )
    assert all(request.headers["Authorization"] == "Basic synthetic" for request in client.requests)


def test_discovery_uses_declarations_without_network() -> None:
    client = _Client([])
    source = WorkdayRaasSource(_config(), _Auth(), client=client)

    discovered = source.discover()

    assert discovered["workers"]["primary_key"] == ["worker_id"]
    assert discovered["workers"]["field_types"]["salary"] == "NUMERIC"
    assert client.requests == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"missing": []}, "data selector"),
        ({"report": {"entries": {}}}, "must be a list"),
        ({"report": {"entries": ["not-a-row"]}}, "non-mapping row"),
    ],
)
def test_workday_source_rejects_malformed_responses(
    payload: object,
    message: str,
) -> None:
    source = WorkdayRaasSource(
        _config(page_size=10),
        _Auth(),
        client=_Client([payload]),
    )

    with pytest.raises(EnterpriseSourceError, match=message):
        list(source.extract("workers"))


def test_cast_failure_names_contract_not_row_value() -> None:
    source = WorkdayRaasSource(
        _config(page_size=10),
        _Auth(),
        client=_Client(
            [
                {
                    "report": {
                        "entries": [
                            {
                                "worker_id": "synthetic-sensitive-value",
                                "active": "not-bool",
                            }
                        ]
                    }
                }
            ]
        ),
    )

    with pytest.raises(EnterpriseSourceError) as raised:
        list(source.extract("workers"))

    assert "active" in str(raised.value)
    assert "not-bool" not in str(raised.value)
    assert "synthetic-sensitive-value" not in str(raised.value)


def test_unknown_endpoint_fails_before_network() -> None:
    client = _Client([])
    source = WorkdayRaasSource(_config(), _Auth(), client=client)

    with pytest.raises(EnterpriseSourceError, match="no endpoint"):
        list(source.extract("absent"))

    assert client.requests == []


def test_workday_source_rejects_unsupported_cursor_pagination() -> None:
    config = _config()
    config.endpoints[0].pagination = CursorPagination(next_cursor_path="meta.next")
    source = WorkdayRaasSource(config, _Auth(), client=_Client([]))

    with pytest.raises(EnterpriseSourceError, match="requires none or page_number"):
        list(source.extract("workers"))


def test_workday_source_retries_with_bounded_declared_backoff() -> None:
    config = _config(page_size=10)
    config.rate_limit = RateLimitConfig(
        requests_per_second=2,
        backoff="exponential",
        max_retries=2,
    )
    delays: list[float] = []
    request = httpx.Request("GET", "https://workday.example.test")
    failure = httpx.ReadTimeout("synthetic timeout", request=request)
    source = WorkdayRaasSource(
        config,
        _Auth(),
        client=_Client([failure, failure, {"report": {"entries": []}}]),
        sleeper=delays.append,
    )

    assert list(source.extract("workers")) == []
    assert delays == [0.5, 1.0]


def test_odoo_source_posts_bounded_pages_and_replays_cursor_boundary() -> None:
    client = _Client(
        [
            [
                {
                    "id": 1,
                    "name": "Acme",
                    "email": False,
                    "active": True,
                    "is_company": True,
                    "write_date": "2026-08-04 10:00:00",
                },
                {
                    "id": 2,
                    "name": "Example",
                    "email": "hello@example.test",
                    "active": True,
                    "is_company": False,
                    "write_date": "2026-08-04 11:00:00",
                },
            ],
            [],
        ]
    )
    auth = _Auth()
    source = OdooJson2Source(_odoo_config(), auth, client=client)

    rows = list(source.extract("partners", since="2026-08-04 09:00:00"))

    assert rows[0]["id"] == 1
    assert rows[0]["email"] is None
    assert rows[0]["is_company"] is True
    assert auth.requests == 2
    first_body = json.loads(client.requests[0].content)
    second_body = json.loads(client.requests[1].content)
    assert first_body == {
        "domain": [["write_date", ">=", "2026-08-04 09:00:00"]],
        "fields": ["id", "name", "email", "active", "is_company", "write_date"],
        "limit": 2,
        "offset": 0,
        "order": "id asc",
    }
    assert second_body["offset"] == 2
    assert all(request.method == "POST" for request in client.requests)
    assert all(request.headers["Authorization"] == "Basic synthetic" for request in client.requests)
    assert all(request.headers["X-Odoo-Database"] == "dander_test" for request in client.requests)


def test_odoo_discovery_uses_declarations_without_network() -> None:
    client = _Client([])
    source = OdooJson2Source(_odoo_config(), _Auth(), client=client)

    discovered = source.discover()

    assert discovered["partners"]["primary_key"] == ["id"]
    assert discovered["partners"]["incremental_cursor"] == "write_date"
    assert client.requests == []


@pytest.mark.parametrize(
    ("config", "payloads", "message"),
    [
        (_odoo_config(), [{"records": []}], "must be a list"),
        (_odoo_config(), [["not-a-row"]], "non-mapping row"),
        (
            _odoo_config().model_copy(
                update={
                    "endpoints": [_odoo_config().endpoints[0].model_copy(update={"raw_schema": []})]
                }
            ),
            [],
            "requires a declared raw schema",
        ),
        (
            _odoo_config().model_copy(
                update={
                    "endpoints": [
                        _odoo_config()
                        .endpoints[0]
                        .model_copy(update={"path": "/json/2/res.partner/read"})
                    ]
                }
            ),
            [],
            "must target",
        ),
    ],
)
def test_odoo_source_rejects_invalid_contracts(
    config: SourceConfig,
    payloads: list[object],
    message: str,
) -> None:
    source = OdooJson2Source(config, _Auth(), client=_Client(payloads))

    with pytest.raises(EnterpriseSourceError, match=message):
        list(source.extract("partners"))


def test_salesforce_bulk2_streams_bounded_pages_and_filters_replay() -> None:
    first_page = _CsvResponse(
        [
            "Id,Name,Type,Industry,AnnualRevenue,NumberOfEmployees,BillingCity,"
            "BillingState,BillingCountry,CreatedDate,LastModifiedDate,SystemModstamp,IsDeleted",
            "001A,Alpha,Customer,Technology,125.50,10,Raleigh,NC,US,"
            "2026-08-01T12:00:00.000Z,2026-08-04T13:00:00.000Z,"
            "2026-08-04T13:00:00.000Z,False",
            "001B,Beta,,,,,,,,2026-08-02T12:00:00.000Z,"
            "2026-08-04T14:00:00.000Z,2026-08-04T14:00:00.000Z,false",
        ],
        locator="next-page",
        records=2,
    )
    second_page = _CsvResponse(
        [
            "Id,Name,Type,Industry,AnnualRevenue,NumberOfEmployees,BillingCity,"
            "BillingState,BillingCountry,CreatedDate,LastModifiedDate,SystemModstamp,IsDeleted",
            "001C,Gamma,Partner,Services,50,3,Durham,NC,US,"
            "2026-08-03T12:00:00.000Z,2026-08-04T15:00:00.000Z,"
            "2026-08-04T15:00:00.000Z,false",
        ],
        locator="null",
        records=1,
    )
    client = _Client(
        [
            {"id": "750-job", "state": "UploadComplete"},
            {"state": "InProgress"},
            {"state": "JobComplete"},
            first_page,
            second_page,
            None,
        ]
    )
    delays: list[float] = []
    source = SalesforceBulk2Source(
        _salesforce_config(),
        _Auth(),
        client=client,
        sleeper=delays.append,
    )

    rows = list(source.extract("accounts", since="2026-08-04T12:30:00+00:00"))

    assert [row["Id"] for row in rows] == ["001A", "001B", "001C"]
    assert rows[1]["AnnualRevenue"] is None
    assert rows[0]["IsDeleted"] is False
    assert client.streams == [False, False, False, True, True, False]
    assert first_page.closed is True
    assert second_page.closed is True
    create_body = json.loads(client.requests[0].content)
    assert create_body["operation"] == "queryAll"
    assert create_body["query"].endswith(
        "FROM Account WHERE SystemModstamp >= 2026-08-04T12:30:00.000Z"
    )
    assert "ORDER BY" not in create_body["query"]
    assert client.requests[3].url.params["maxRecords"] == "2"
    assert "locator" not in client.requests[3].url.params
    assert client.requests[4].url.params["locator"] == "next-page"
    assert client.requests[-1].method == "DELETE"
    assert delays == [1.0]


def test_salesforce_bulk2_initial_query_has_no_watermark_filter() -> None:
    page = _CsvResponse(
        [
            "Id,Name,CreatedDate,LastModifiedDate,SystemModstamp,IsDeleted",
        ],
        locator="null",
        records=0,
    )
    client = _Client(
        [
            {"id": "750-empty", "state": "UploadComplete"},
            {"state": "JobComplete"},
            page,
            None,
        ]
    )
    source = SalesforceBulk2Source(_salesforce_config(), _Auth(), client=client)

    assert list(source.extract("accounts")) == []
    assert " WHERE " not in json.loads(client.requests[0].content)["query"]
    assert client.requests[-1].method == "DELETE"


def test_salesforce_bulk2_failed_job_is_clear_and_cleaned_up() -> None:
    client = _Client(
        [
            {"id": "750-failed", "state": "UploadComplete"},
            {"state": "Failed", "errorMessage": "synthetic query failure"},
            None,
        ]
    )
    source = SalesforceBulk2Source(_salesforce_config(), _Auth(), client=client)

    with pytest.raises(EnterpriseSourceError, match="ended in Failed") as raised:
        list(source.extract("accounts"))

    assert "synthetic query failure" in str(raised.value)
    assert client.requests[-1].method == "DELETE"


def test_salesforce_bulk2_rejects_malformed_csv_before_publication() -> None:
    malformed = _CsvResponse(
        [
            "Id,Name,CreatedDate,LastModifiedDate,SystemModstamp,IsDeleted,Unexpected",
            "001A,Alpha,2026-08-01T12:00:00.000Z,2026-08-04T13:00:00.000Z,"
            "2026-08-04T13:00:00.000Z,false,surprise",
        ],
        locator="null",
        records=1,
    )
    client = _Client(
        [
            {"id": "750-malformed", "state": "UploadComplete"},
            {"state": "JobComplete"},
            malformed,
            None,
        ]
    )
    source = SalesforceBulk2Source(_salesforce_config(), _Auth(), client=client)

    with pytest.raises(EnterpriseSourceError, match="undeclared Salesforce field"):
        list(source.extract("accounts"))

    assert malformed.closed is True
    assert client.requests[-1].method == "DELETE"
