"""Live-loopback contract tests for Dander's stateful Workday RaaS simulator."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import yaml

from dander.dev.workday_simulator import WorkdaySimulatorServer, create_workday_simulator
from dander.ingestion import EnterpriseSourceError, WorkdayRaasSource, load_source_config
from dander.ingestion.source import Endpoint, RateLimitConfig, SourceConfig
from dander.security import OAuth2ClientCredentials, OAuthTokenError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_CONTRACT = Path(__file__).parents[2] / "contracts" / "workday-raas-simulator.openapi.yaml"
_CONNECTOR = Path(__file__).parents[2] / "connectors" / "workday_raas.example.yaml"
_TOKEN_URL = "https://workday.example.test/ccx/oauth2/dander-tenant/token"


class _Secrets:
    def get_secret(self, reference: str) -> str:
        return {
            "workday-client-id": "dander-client",
            "workday-client-secret": "dander-secret",
        }[reference]


class _LoopbackTokenRequester:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def __call__(
        self,
        url: str,
        *,
        auth: tuple[str, str] | None,
        data: Mapping[str, str],
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert url == _TOKEN_URL
        return httpx.post(
            f"{self._base_url}/ccx/oauth2/dander-tenant/token",
            auth=auth,
            data=data,
            params=params,
            headers=headers,
            timeout=timeout,
        )


class _LoopbackEnterpriseClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def send(self, request: httpx.Request) -> httpx.Response:
        return self._client.send(request)


@pytest.fixture
def workday_server() -> Iterator[WorkdaySimulatorServer]:
    with WorkdaySimulatorServer() as server:
        yield server


def _config(base_url: str, *, page_size: int = 2) -> SourceConfig:
    pagination = {
        "kind": "page_number",
        "page_param": "page",
        "size_param": "count",
        "page_size": page_size,
        "start_page": 1,
    }
    return SourceConfig(
        name="workday_raas_simulator",
        base_url=(f"{base_url}/ccx/service/customreport2/dander-tenant"),
        auth_strategy="oauth2_client_credentials",
        auth_refs={
            "client_id": "workday-client-id",
            "client_secret": "workday-client-secret",
        },
        auth_options={"token_url": _TOKEN_URL},
        rate_limit=RateLimitConfig(
            requests_per_second=2,
            backoff="exponential",
            max_retries=2,
        ),
        endpoints=[
            Endpoint(
                name="workers",
                path="dander-is/Dander_Workers",
                pagination=pagination,
                data_selector="Report_Entry",
                incremental_cursor="updated_at",
                cursor_param="updated_after",
                primary_key=["worker_id"],
                field_types={
                    "worker_id": "STRING",
                    "active": "BOOL",
                    "base_pay": "NUMERIC",
                    "hire_date": "DATE",
                    "updated_at": "TIMESTAMP",
                },
            ),
            Endpoint(
                name="organizations",
                path="dander-is/Dander_Organizations",
                pagination=pagination,
                data_selector="Report_Entry",
                incremental_cursor="updated_at",
                cursor_param="updated_after",
                primary_key=["organization_id"],
                field_types={
                    "organization_id": "STRING",
                    "active": "BOOL",
                    "updated_at": "TIMESTAMP",
                },
            ),
        ],
    )


def _source(
    server: WorkdaySimulatorServer,
    client: httpx.Client,
    *,
    page_size: int = 2,
    delays: list[float] | None = None,
) -> WorkdayRaasSource:
    auth = OAuth2ClientCredentials(
        _Secrets(),
        client_id_ref="workday-client-id",
        client_secret_ref="workday-client-secret",
        token_url=_TOKEN_URL,
        request_token=_LoopbackTokenRequester(server.base_url),
    )
    return WorkdayRaasSource(
        _config(server.base_url, page_size=page_size),
        auth,
        client=_LoopbackEnterpriseClient(client),
        sleeper=(delays if delays is not None else []).append,
    )


def _set_scenario(server: WorkdaySimulatorServer, scenario: str) -> None:
    response = httpx.put(
        f"{server.base_url}/_dander/scenario",
        json={"scenario": scenario},
    )
    response.raise_for_status()


def test_tracked_openapi_contract_matches_the_fastapi_operations() -> None:
    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    generated = create_workday_simulator().openapi()
    expected = {
        (path, method, operation["operationId"])
        for path, methods in contract["paths"].items()
        for method, operation in methods.items()
    }
    actual = {
        (path, method, operation["operationId"])
        for path, methods in generated["paths"].items()
        for method, operation in methods.items()
        if operation["operationId"] in {item[2] for item in expected}
    }

    assert actual == expected
    assert {operation_id for _, _, operation_id in expected} == {
        "issueAccessToken",
        "getWorkersReport",
        "getOrganizationsReport",
        "setScenario",
        "advanceDataset",
        "resetSimulator",
    }


def test_example_connector_matches_the_two_report_contracts() -> None:
    config = load_source_config(_CONNECTOR)

    assert str(config.engine) == "workday_raas"
    assert [endpoint.name for endpoint in config.endpoints] == ["workers", "organizations"]
    assert [endpoint.path for endpoint in config.endpoints] == [
        "REPORT_OWNER/Dander_Workers",
        "REPORT_OWNER/Dander_Organizations",
    ]
    assert all(endpoint.data_selector == "Report_Entry" for endpoint in config.endpoints)
    assert all(endpoint.raw_schema is not None for endpoint in config.endpoints)


def test_dander_extracts_paged_stateful_workers_and_organizations(
    workday_server: WorkdaySimulatorServer,
) -> None:
    delays: list[float] = []
    with httpx.Client(timeout=5) as client:
        source = _source(workday_server, client, delays=delays)

        workers = list(source.extract("workers"))
        organizations = list(source.extract("organizations"))
        advanced = httpx.post(f"{workday_server.base_url}/_dander/advance")
        advanced.raise_for_status()
        changed_workers = list(source.extract("workers", since="2026-08-02T00:00:00Z"))

    assert [row["worker_id"] for row in workers] == ["W-1001", "W-1002", "W-1003"]
    assert workers[0]["active"] is True
    assert workers[0]["base_pay"] == Decimal("125000.00")
    assert workers[0]["hire_date"] == date(2024, 2, 12)
    assert workers[0]["updated_at"] == datetime(2026, 8, 1, 13, tzinfo=UTC)
    assert [row["organization_id"] for row in organizations] == [
        "ORG-ENG",
        "ORG-FIN",
        "ORG-PEOPLE",
    ]
    assert [row["worker_id"] for row in changed_workers] == ["W-1001", "W-1004"]
    assert changed_workers[0]["business_title"] == "Senior Data Platform Engineer"
    assert workday_server.snapshot() == {
        "scenario": "normal",
        "generation": 1,
        "requests": {
            "token": 1,
            "workers:1": 2,
            "workers:2": 2,
            "organizations:1": 1,
            "organizations:2": 1,
        },
    }
    assert delays == [0.5, 0.5, 0.5]


def test_expired_credentials_fail_without_exposing_the_secret(
    workday_server: WorkdaySimulatorServer,
) -> None:
    _set_scenario(workday_server, "expired_credentials")
    with httpx.Client(timeout=5) as client:
        source = _source(workday_server, client, page_size=100)
        with pytest.raises(OAuthTokenError, match="OAuth token request failed") as raised:
            list(source.extract("workers"))

    assert "dander-secret" not in str(raised.value)


def test_throttling_retries_once_with_declared_backoff(
    workday_server: WorkdaySimulatorServer,
) -> None:
    _set_scenario(workday_server, "throttling")
    delays: list[float] = []
    with httpx.Client(timeout=5) as client:
        source = _source(workday_server, client, page_size=2, delays=delays)
        rows = list(source.extract("workers"))

    assert len(rows) == 3
    assert delays == [0.5, 0.5]
    assert workday_server.snapshot()["requests"] == {
        "token": 1,
        "workers:1": 2,
        "workers:2": 1,
    }


def test_missing_permissions_fail_without_retrying_or_leaking_a_response(
    workday_server: WorkdaySimulatorServer,
) -> None:
    _set_scenario(workday_server, "missing_permissions")
    with httpx.Client(timeout=5) as client:
        source = _source(workday_server, client, page_size=100)
        with pytest.raises(EnterpriseSourceError, match="permission denied") as raised:
            list(source.extract("organizations"))

    assert "insufficient_permissions" not in str(raised.value)
    assert workday_server.snapshot()["requests"] == {"token": 1, "organizations:1": 1}


def test_malformed_record_names_only_the_broken_field_contract(
    workday_server: WorkdaySimulatorServer,
) -> None:
    _set_scenario(workday_server, "malformed_record")
    with httpx.Client(timeout=5) as client:
        source = _source(workday_server, client, page_size=100)
        with pytest.raises(EnterpriseSourceError, match="field 'active'") as raised:
            list(source.extract("workers"))

    message = str(raised.value)
    assert "not-a-valid-boolean" not in message
    assert "Ari Quill" not in message
