"""Metadata-only BigQuery input-size estimation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from dander.control.bigquery_input_size_estimator import BigQueryInputSizeEstimator
from dander.control.graph_store import GraphRecord
from dander.control.input_size_estimator import InputSizeEstimationError
from dander.control.models import PipelineGraphDocument

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


@dataclass
class _Response:
    payload: object
    status_code: int = 200

    def json(self) -> object:
        return self.payload


@dataclass
class _Transport:
    responses: list[_Response]
    calls: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    closed: bool = False

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _graph() -> GraphRecord:
    document = PipelineGraphDocument.model_validate(
        {
            "name": "keyed_join",
            "nodes": [
                {
                    "id": "comments",
                    "type": "source",
                    "name": "Comments",
                    "config": {
                        "connector": "keyed_join_fixture",
                        "endpoint": "comments",
                    },
                },
                {
                    "id": "posts",
                    "type": "source",
                    "name": "Posts",
                    "config": {
                        "connector": "keyed_join_fixture",
                        "endpoint": "posts",
                    },
                },
            ],
            "edges": [],
        }
    )
    return GraphRecord(
        project="demo",
        graph="keyed-join",
        document=document,
        revision="graph-r1",
        content_sha256="a" * 64,
        created_at="2026-08-28T12:00:00Z",
        updated_at="2026-08-28T12:00:00Z",
    )


def test_bigquery_estimator_sums_unique_source_table_metadata() -> None:
    transport = _Transport([_Response({"numBytes": "300"}), _Response({"numBytes": "700"})])
    estimator = BigQueryInputSizeEstimator(
        "dander-unit-project",
        "raw",
        transport=transport,
        clock=lambda: NOW,
    )

    estimate = estimator.estimate(_graph())

    assert estimate.estimated_input_bytes == 1_000
    assert estimate.source == "bigquery_table_metadata"
    assert estimate.observed_at == NOW
    assert [call[1].rsplit("/", 1)[-1] for call in transport.calls] == [
        "keyed_join_fixture_comments",
        "keyed_join_fixture_posts",
    ]
    assert all(call[0] == "GET" for call in transport.calls)
    assert all(call[2]["params"] == {"view": "STORAGE_STATS"} for call in transport.calls)
    assert transport.closed is False


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response({}, 503), "unavailable"),
        (_Response({"numBytes": 100}), "invalid"),
        (_Response({"numBytes": str(9_223_372_036_854_775_808)}), "exceeds"),
    ],
)
def test_bigquery_estimator_normalizes_invalid_or_unavailable_metadata(
    response: _Response,
    message: str,
) -> None:
    estimator = BigQueryInputSizeEstimator(
        "dander-unit-project",
        "raw",
        transport=_Transport([response]),
    )

    with pytest.raises(InputSizeEstimationError, match=message):
        estimator.estimate(_graph())


def test_bigquery_estimator_normalizes_transient_identity_failure() -> None:
    def unavailable_identity() -> object:
        raise TimeoutError("credential endpoint timed out")

    estimator = BigQueryInputSizeEstimator(
        "dander-unit-project",
        "raw",
        credential_factory=unavailable_identity,
    )

    with pytest.raises(InputSizeEstimationError, match="identity is unavailable"):
        estimator.estimate(_graph())
