"""Loopback contract tests for Dander's stateful NetSuite SuiteQL simulator."""

from __future__ import annotations

from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
import yaml

from dander.cli.main import _build_source_adapter
from dander.dev.netsuite_simulator import (
    NetSuiteSimulatorServer,
    create_netsuite_simulator,
)
from dander.ingestion import (
    EnterpriseSourceError,
    IngestionEngine,
    NetSuiteSuiteQLSource,
    OffsetPagination,
    load_source_config,
)
from dander.runtime import PipelineRunner, RawSchemaError
from dander.security import OAuth1TBA
from dander.state import SqliteWatermarkStore
from dander.writer import WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

_CONTRACT = Path(__file__).parents[2] / "contracts" / "netsuite-suiteql-simulator.openapi.yaml"
_CONNECTOR = Path(__file__).parents[2] / "connectors" / "netsuite.example.yaml"


class _Secrets:
    def get_secret(self, reference: str) -> str:
        return {
            "netsuite-consumer-key": "dander-consumer-key",
            "netsuite-consumer-secret": "dander-consumer-secret",
            "netsuite-token-id": "dander-token-id",
            "netsuite-token-secret": "dander-token-secret",
        }[reference]


class _CapturingWriter(WritePattern):
    mode = WriteMode.SCD1
    supports_batched_writes = True

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        assert target.table == "netsuite_customers"
        batch = [dict(record) for record in records]
        for row in batch:
            self.rows[str(row["id"])] = row
        return len(batch)


@pytest.fixture
def netsuite_server() -> Iterator[NetSuiteSimulatorServer]:
    with NetSuiteSimulatorServer() as server:
        yield server


def _source(
    server: NetSuiteSimulatorServer,
    *,
    page_size: int = 2,
    delays: list[float] | None = None,
) -> NetSuiteSuiteQLSource:
    config = load_source_config(_CONNECTOR).model_copy(deep=True)
    config.base_url = f"{server.base_url}/services/rest/query/v1"
    config.auth_options["account_id"] = "DANDER_SB1"
    config.auth_refs = {
        "consumer_key": "netsuite-consumer-key",
        "consumer_secret": "netsuite-consumer-secret",
        "token_id": "netsuite-token-id",
        "token_secret": "netsuite-token-secret",
    }
    config.endpoints[0].pagination = OffsetPagination(page_size=page_size)
    nonce_sequence = count(1)
    auth = OAuth1TBA(
        _Secrets(),
        account_id="DANDER_SB1",
        consumer_key_ref="netsuite-consumer-key",
        consumer_secret_ref="netsuite-consumer-secret",
        token_id_ref="netsuite-token-id",
        token_secret_ref="netsuite-token-secret",
        nonce=lambda: f"dander-nonce-{next(nonce_sequence)}",
        clock=lambda: 1_775_000_000,
    )
    return NetSuiteSuiteQLSource(
        config,
        auth,
        client=cast("Any", httpx.Client(timeout=5)),
        sleeper=(delays if delays is not None else []).append,
    )


def _set_scenario(server: NetSuiteSimulatorServer, scenario: str) -> None:
    response = httpx.put(f"{server.base_url}/_dander/scenario", json={"scenario": scenario})
    response.raise_for_status()


def test_tracked_openapi_contract_matches_the_six_fastapi_operations() -> None:
    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    generated = create_netsuite_simulator().openapi()
    expected = {
        (path, method, operation["operationId"])
        for path, methods in contract["paths"].items()
        for method, operation in methods.items()
        if method != "parameters"
    }
    actual = {
        (path, method, operation["operationId"])
        for path, methods in generated["paths"].items()
        for method, operation in methods.items()
        if method != "parameters" and operation["operationId"] in {item[2] for item in expected}
    }

    assert actual == expected
    assert {operation_id for _, _, operation_id in expected} == {
        "executeSuiteQL",
        "createCustomer",
        "updateCustomer",
        "deleteCustomer",
        "setScenario",
        "resetSimulator",
    }


def test_example_connector_uses_the_narrow_suiteql_contract() -> None:
    config = load_source_config(_CONNECTOR)
    endpoint = config.endpoints[0]

    assert config.engine is IngestionEngine.NETSUITE_SUITEQL
    assert config.base_url.endswith("/services/rest/query/v1")
    assert endpoint.path == "/suiteql"
    assert str(endpoint.request_body["q"]).endswith("FROM customer ORDER BY id")
    assert endpoint.pagination == OffsetPagination(page_size=1000)
    assert endpoint.incremental_cursor == "last_modified_at"
    assert endpoint.cursor_param == ""
    assert [field.name for field in endpoint.raw_schema] == [
        "id",
        "entity_id",
        "company_name",
        "email",
        "phone",
        "date_created_at",
        "last_modified_at",
        "is_inactive",
    ]
    auth = OAuth1TBA(
        _Secrets(),
        account_id="DANDER_SB1",
        consumer_key_ref="netsuite-consumer-key",
        consumer_secret_ref="netsuite-consumer-secret",
        token_id_ref="netsuite-token-id",
        token_secret_ref="netsuite-token-secret",
    )
    assert isinstance(_build_source_adapter(config, auth), NetSuiteSuiteQLSource)


def test_signed_suiteql_paginates_and_drops_transport_links(
    netsuite_server: NetSuiteSimulatorServer,
) -> None:
    rows = list(_source(netsuite_server).extract("customers"))

    assert [row["id"] for row in rows] == ["1001", "1002", "1003", "1004", "1005"]
    assert all("links" not in row for row in rows)
    assert netsuite_server.snapshot()["requests"] == {
        "suiteql:0": 1,
        "suiteql:2": 1,
        "suiteql:4": 1,
    }


def test_stateful_update_replay_is_duplicate_free_and_watermark_is_monotonic(
    netsuite_server: NetSuiteSimulatorServer,
    tmp_path: Path,
) -> None:
    source = _source(netsuite_server)
    writer = _CapturingWriter()
    watermarks = SqliteWatermarkStore(tmp_path / "state.db")
    runner = PipelineRunner(
        source=source,
        writer=writer,
        watermarks=watermarks,
        project="synthetic-project",
        dataset="raw",
        batch_rows=2,
    )

    first = runner.run(run_id="netsuite-first")
    first_cursor = first.endpoints[0].committed_cursor
    created_response = httpx.post(
        f"{netsuite_server.base_url}/_dander/customer",
        json={
            "entity_id": "CUST-DANDER",
            "company_name": "Dander Acceptance Customer",
            "email": "acceptance@dander.example",
        },
    )
    created_response.raise_for_status()
    created = created_response.json()
    update_response = httpx.patch(
        f"{netsuite_server.base_url}/_dander/customer/{created['id']}",
        json={"company_name": "Dander Acceptance Customer Updated"},
    )
    update_response.raise_for_status()

    second = runner.run(run_id="netsuite-update")
    replay_rows = dict(writer.rows)
    replay = runner.run(run_id="netsuite-replay")
    delete_response = httpx.delete(f"{netsuite_server.base_url}/_dander/customer/{created['id']}")
    delete_response.raise_for_status()

    assert first.endpoints[0].extracted == 5
    assert second.endpoints[0].extracted == 6
    assert replay.endpoints[0].extracted == 6
    assert first_cursor is not None
    assert second.endpoints[0].committed_cursor is not None
    assert second.endpoints[0].committed_cursor >= first_cursor
    assert replay.endpoints[0].committed_cursor == second.endpoints[0].committed_cursor
    assert len(writer.rows) == 6
    assert writer.rows == replay_rows
    assert writer.rows[str(created["id"])]["company_name"].endswith("Updated")
    assert watermarks.get("netsuite", "customers") == replay.endpoints[0].committed_cursor
    assert netsuite_server.snapshot()["records"] == 5


def test_throttling_retries_the_same_suiteql_page_once(
    netsuite_server: NetSuiteSimulatorServer,
) -> None:
    _set_scenario(netsuite_server, "throttling")
    delays: list[float] = []

    rows = list(_source(netsuite_server, page_size=100, delays=delays).extract("customers"))

    assert len(rows) == 5
    assert delays == [0.5]
    assert netsuite_server.snapshot()["requests"] == {"suiteql:0": 2}


def test_expired_credentials_and_bad_signatures_fail_without_exposing_secrets(
    netsuite_server: NetSuiteSimulatorServer,
) -> None:
    unsigned = httpx.post(
        f"{netsuite_server.base_url}/services/rest/query/v1/suiteql",
        params={"limit": 1, "offset": 0},
        headers={"Prefer": "transient"},
        json={"q": load_source_config(_CONNECTOR).endpoints[0].request_body["q"]},
    )
    assert unsigned.status_code == 401

    _set_scenario(netsuite_server, "expired_credentials")
    with pytest.raises(EnterpriseSourceError, match="authentication failed") as raised:
        list(_source(netsuite_server, page_size=100).extract("customers"))

    assert "dander-consumer-secret" not in str(raised.value)
    assert "dander-token-secret" not in str(raised.value)


def test_missing_permissions_fail_without_retry(
    netsuite_server: NetSuiteSimulatorServer,
) -> None:
    _set_scenario(netsuite_server, "missing_permissions")

    with pytest.raises(EnterpriseSourceError, match="permission denied"):
        list(_source(netsuite_server, page_size=100).extract("customers"))

    assert netsuite_server.snapshot()["requests"] == {"suiteql:0": 1}


def test_malformed_response_metadata_fails_closed(
    netsuite_server: NetSuiteSimulatorServer,
) -> None:
    _set_scenario(netsuite_server, "malformed_response")

    with pytest.raises(EnterpriseSourceError, match="invalid SuiteQL page metadata"):
        list(_source(netsuite_server, page_size=100).extract("customers"))


def test_malformed_record_fails_the_declared_raw_schema(
    netsuite_server: NetSuiteSimulatorServer,
    tmp_path: Path,
) -> None:
    _set_scenario(netsuite_server, "malformed_record")
    runner = PipelineRunner(
        source=_source(netsuite_server, page_size=100),
        writer=_CapturingWriter(),
        watermarks=SqliteWatermarkStore(tmp_path / "state.db"),
        project="synthetic-project",
        dataset="raw",
    )

    with pytest.raises(RawSchemaError, match="Scalar field has a structured value"):
        runner.run(run_id="netsuite-malformed")
