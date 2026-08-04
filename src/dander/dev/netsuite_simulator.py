"""Stateful, loopback-only NetSuite SuiteQL simulator for integration tests.

The simulator implements the exact six-operation contract documented in
``docs/netsuite-simulator.md``. Its records and OAuth1 credentials are invented; it makes no
claim that Dander has passed acceptance against a real NetSuite tenant.
"""

from __future__ import annotations

import argparse
import json
import socket
from base64 import b64encode
from collections import Counter
from contextlib import AbstractContextManager, suppress
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from hmac import new as new_hmac
from importlib import resources
from secrets import compare_digest
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote, unquote

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel

_ACCOUNT_ID = "DANDER_SB1"
_CONSUMER_KEY = "dander-consumer-key"
_CONSUMER_SECRET = "dander-consumer-secret"
_TOKEN_ID = "dander-token-id"
_TOKEN_SECRET = "dander-token-secret"
_CUSTOMER_QUERY = " ".join(
    (
        "SELECT id, entityid AS entity_id, companyname AS company_name, email, phone,",
        "TO_CHAR(datecreated, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS date_created_at,",
        "TO_CHAR(lastmodifieddate, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS last_modified_at,",
        "isinactive AS is_inactive FROM customer ORDER BY id",
    )
)


class NetSuiteScenario(StrEnum):
    """Named deterministic behaviors exposed by the simulator control API."""

    NORMAL = "normal"
    EXPIRED_CREDENTIALS = "expired_credentials"
    THROTTLING = "throttling"
    MISSING_PERMISSIONS = "missing_permissions"
    MALFORMED_RECORD = "malformed_record"
    MALFORMED_RESPONSE = "malformed_response"


class ScenarioRequest(BaseModel):
    """Select one simulator behavior."""

    scenario: NetSuiteScenario


class SuiteQLRequest(BaseModel):
    """Official SuiteQL request body used by the narrow connector."""

    q: str


class CustomerCreate(BaseModel):
    """Writable fields used only to prepare simulator acceptance state."""

    entity_id: str
    company_name: str
    email: str = ""
    phone: str = ""
    is_inactive: Literal["T", "F"] = "F"


class CustomerUpdate(BaseModel):
    """Patch fields used only to advance simulator acceptance state."""

    entity_id: str | None = None
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    is_inactive: Literal["T", "F"] | None = None


class _SimulatorState:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._lock = Lock()
        self._initial = [dict(row) for row in rows]
        self._rows = {str(row["id"]): dict(row) for row in rows}
        self._scenario = NetSuiteScenario.NORMAL
        self._requests: Counter[str] = Counter()
        self._throttle_consumed = False
        self._clock = datetime(2026, 8, 4, 13, 0, 0)
        self._next_id = 9001

    def reset(self) -> None:
        with self._lock:
            self._rows = {str(row["id"]): dict(row) for row in self._initial}
            self._scenario = NetSuiteScenario.NORMAL
            self._requests.clear()
            self._throttle_consumed = False
            self._clock = datetime(2026, 8, 4, 13, 0, 0)
            self._next_id = 9001

    def set_scenario(self, scenario: NetSuiteScenario) -> None:
        with self._lock:
            self._scenario = scenario
            self._requests.clear()
            self._throttle_consumed = False

    def scenario(self) -> NetSuiteScenario:
        with self._lock:
            return self._scenario

    def request_number(self, key: str) -> int:
        with self._lock:
            self._requests[key] += 1
            return self._requests[key]

    def consume_throttle(self) -> bool:
        with self._lock:
            if self._throttle_consumed:
                return False
            self._throttle_consumed = True
            return True

    def list_rows(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(row) for row in self._rows.values()]

    def create(self, payload: CustomerCreate) -> dict[str, object]:
        with self._lock:
            timestamp = self._tick()
            customer_id = str(self._next_id)
            self._next_id += 1
            row: dict[str, object] = {
                "id": customer_id,
                "entity_id": payload.entity_id,
                "company_name": payload.company_name,
                "email": payload.email,
                "phone": payload.phone,
                "date_created_at": timestamp,
                "last_modified_at": timestamp,
                "is_inactive": payload.is_inactive,
            }
            self._rows[customer_id] = row
            return dict(row)

    def update(self, customer_id: str, payload: CustomerUpdate) -> dict[str, object]:
        with self._lock:
            row = self._rows.get(customer_id)
            if row is None:
                raise HTTPException(status_code=404, detail={"error": "record_not_found"})
            row.update(payload.model_dump(exclude_none=True))
            row["last_modified_at"] = self._tick()
            return dict(row)

    def delete(self, customer_id: str) -> None:
        with self._lock:
            if self._rows.pop(customer_id, None) is None:
                raise HTTPException(status_code=404, detail={"error": "record_not_found"})

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "scenario": self._scenario.value,
                "records": len(self._rows),
                "requests": dict(self._requests),
            }

    def _tick(self) -> str:
        self._clock += timedelta(seconds=1)
        return self._clock.strftime("%Y-%m-%dT%H:%M:%S")


def create_netsuite_simulator() -> FastAPI:
    """Build a fresh simulator app with isolated mutable state."""
    state = _SimulatorState(_load_rows())
    app = FastAPI(
        title="Dander NetSuite SuiteQL simulator",
        version="1.0.0",
        description="Synthetic contract for Dander's simulator-validated NetSuite slice.",
    )
    app.state.simulator = state

    @app.post(
        "/services/rest/query/v1/suiteql",
        operation_id="executeSuiteQL",
        tags=["netsuite-contract"],
    )
    def execute_suiteql(
        payload: SuiteQLRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        prefer: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        state.request_number(f"suiteql:{offset}")
        if state.scenario() is NetSuiteScenario.EXPIRED_CREDENTIALS:
            _fail(401, "INVALID_LOGIN_ATTEMPT", "The token is expired or invalid.")
        if not _valid_oauth1(request, authorization):
            _fail(401, "INVALID_LOGIN_ATTEMPT", "The OAuth1 signature is invalid.")
        if state.scenario() is NetSuiteScenario.MISSING_PERMISSIONS:
            _fail(403, "INSUFFICIENT_PERMISSION", "SuiteAnalytics Workbook permission is required.")
        if state.scenario() is NetSuiteScenario.THROTTLING and state.consume_throttle():
            raise HTTPException(
                status_code=429,
                detail={"type": "CONCURRENCY_LIMIT_EXCEEDED", "status": 429},
                headers={"Retry-After": "0"},
            )
        if prefer != "transient":
            _fail(400, "INVALID_HEADER", "Prefer: transient is required.")
        if " ".join(payload.q.split()) != _CUSTOMER_QUERY:
            _fail(400, "INVALID_QUERY", "The simulator accepts only the customer contract query.")

        rows = sorted(state.list_rows(), key=lambda row: int(str(row["id"])))
        page = [dict(row, links=[]) for row in rows[offset : offset + limit]]
        if state.scenario() is NetSuiteScenario.MALFORMED_RECORD and page:
            page[0]["company_name"] = {"value": "malformed"}
        response: dict[str, object] = {
            "links": [{"rel": "self", "href": str(request.url)}],
            "count": len(page),
            "offset": offset,
            "totalResults": len(rows),
            "hasMore": offset + len(page) < len(rows),
            "items": page,
        }
        if state.scenario() is NetSuiteScenario.MALFORMED_RESPONSE:
            response["count"] = "not-an-integer"
        return response

    @app.post(
        "/_dander/customer",
        operation_id="createCustomer",
        tags=["acceptance-setup"],
    )
    def create_customer(payload: CustomerCreate) -> dict[str, object]:
        state.request_number("create")
        return state.create(payload)

    @app.patch(
        "/_dander/customer/{customer_id}",
        operation_id="updateCustomer",
        tags=["acceptance-setup"],
    )
    def update_customer(
        payload: CustomerUpdate,
        customer_id: Annotated[str, Path(pattern=r"^[0-9]+$")],
    ) -> dict[str, object]:
        state.request_number("update")
        return state.update(customer_id, payload)

    @app.delete(
        "/_dander/customer/{customer_id}",
        operation_id="deleteCustomer",
        tags=["acceptance-setup"],
        status_code=204,
    )
    def delete_customer(
        customer_id: Annotated[str, Path(pattern=r"^[0-9]+$")],
    ) -> Response:
        state.request_number("delete")
        state.delete(customer_id)
        return Response(status_code=204)

    @app.put(
        "/_dander/scenario",
        operation_id="setScenario",
        tags=["simulator-control"],
    )
    def set_scenario(payload: ScenarioRequest) -> dict[str, str]:
        state.set_scenario(payload.scenario)
        return {"scenario": payload.scenario.value}

    @app.post(
        "/_dander/reset",
        operation_id="resetSimulator",
        tags=["simulator-control"],
    )
    def reset_simulator() -> dict[str, str]:
        state.reset()
        return {"status": "reset"}

    return app


def _load_rows() -> list[dict[str, object]]:
    fixture = resources.files("dander.dev.fixtures.netsuite").joinpath("customers.json")
    payload: Any = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("Invalid packaged NetSuite customer fixture")
    return [dict(cast("dict[str, object]", row)) for row in payload]


def _valid_oauth1(request: Request, authorization: str | None) -> bool:
    parameters = _oauth_header(authorization)
    required = {
        "realm",
        "oauth_consumer_key",
        "oauth_nonce",
        "oauth_signature",
        "oauth_signature_method",
        "oauth_timestamp",
        "oauth_token",
        "oauth_version",
    }
    if set(parameters) != required:
        return False
    if (
        parameters["realm"] != _ACCOUNT_ID
        or parameters["oauth_consumer_key"] != _CONSUMER_KEY
        or parameters["oauth_token"] != _TOKEN_ID
        or parameters["oauth_signature_method"] != "HMAC-SHA256"
        or parameters["oauth_version"] != "1.0"
        or not parameters["oauth_nonce"]
    ):
        return False
    try:
        int(parameters["oauth_timestamp"])
    except ValueError:
        return False

    url = httpx.URL(str(request.url))
    oauth = {
        key: value for key, value in parameters.items() if key not in {"realm", "oauth_signature"}
    }
    normalized = "&".join(
        f"{key}={value}"
        for key, value in sorted(
            (_encode(key), _encode(value))
            for key, value in [*url.params.multi_items(), *oauth.items()]
        )
    )
    base_string = "&".join(
        (_encode(request.method.upper()), _encode(_base_uri(url)), _encode(normalized))
    )
    signing_key = f"{_encode(_CONSUMER_SECRET)}&{_encode(_TOKEN_SECRET)}"
    expected = b64encode(
        new_hmac(signing_key.encode(), base_string.encode(), sha256).digest()
    ).decode("ascii")
    return compare_digest(parameters["oauth_signature"], expected)


def _oauth_header(authorization: str | None) -> dict[str, str]:
    if authorization is None or not authorization.startswith("OAuth "):
        return {}
    parsed: dict[str, str] = {}
    for item in authorization.removeprefix("OAuth ").split(","):
        key, separator, value = item.strip().partition("=")
        if not separator or len(value) < 2 or value[0] != '"' or value[-1] != '"':
            return {}
        parsed[key] = unquote(value[1:-1])
    return parsed


def _base_uri(url: httpx.URL) -> str:
    authority = url.host.lower()
    if url.port is not None and not (
        (url.scheme == "http" and url.port == 80) or (url.scheme == "https" and url.port == 443)
    ):
        authority = f"{authority}:{url.port}"
    return f"{url.scheme.lower()}://{authority}{url.path or '/'}"


def _encode(value: str) -> str:
    return quote(value, safe="~-._")


def _fail(status: int, code: str, detail: str) -> None:
    raise HTTPException(
        status_code=status,
        detail={
            "type": "https://www.rfc-editor.org/rfc/rfc9110.html",
            "title": "Request failed",
            "status": status,
            "o:errorDetails": [{"detail": detail, "o:errorCode": code}],
        },
    )


class NetSuiteSimulatorServer(AbstractContextManager["NetSuiteSimulatorServer"]):
    """Own a background NetSuite simulator bound to loopback by default."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.app = create_netsuite_simulator()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        bound_host, bound_port = cast("tuple[str, int]", self._socket.getsockname())
        self._host = bound_host
        self._port = bound_port
        self._server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="critical", access_log=False, lifespan="off")
        )
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        """Return the bound loopback URL."""
        return f"http://{self._host}:{self._port}"

    def start(self) -> NetSuiteSimulatorServer:
        """Start serving and wait until uvicorn accepts requests."""
        if self._thread is not None:
            return self
        self._thread = Thread(target=self._serve, name="dander-netsuite-simulator", daemon=True)
        self._thread.start()
        deadline = monotonic() + 5
        while not self._server.started:
            if not self._thread.is_alive() or monotonic() >= deadline:
                raise RuntimeError("NetSuite simulator did not start")
            sleep(0.01)
        return self

    def _serve(self) -> None:
        self._server.run(sockets=[self._socket])

    def snapshot(self) -> dict[str, object]:
        """Return sanitized request counters and state."""
        state = cast("_SimulatorState", self.app.state.simulator)
        return state.snapshot()

    def wait(self) -> None:
        """Wait for a foreground invocation until interrupted."""
        if self._thread is None:
            raise RuntimeError("NetSuite simulator is not running")
        while self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def close(self) -> None:
        """Stop the service and release its socket."""
        if self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=5)
            self._thread = None
        if self._socket.fileno() != -1:
            self._socket.close()

    def __enter__(self) -> NetSuiteSimulatorServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self.close()


def main() -> None:
    """Run the simulator until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    with NetSuiteSimulatorServer(args.host, args.port) as server:
        print(f"Dander NetSuite simulator listening at {server.base_url}", flush=True)
        print("Synthetic account: DANDER_SB1", flush=True)
        with suppress(KeyboardInterrupt):
            server.wait()


if __name__ == "__main__":
    main()
