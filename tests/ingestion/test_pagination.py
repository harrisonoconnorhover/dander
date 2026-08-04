"""Unit tests for the pagination strategy model (DANDER-12).

Covers: each pagination kind parsing with its typed params, the bare-string coercion shorthand on
`Endpoint.pagination`, rejection of an out-of-set kind, rejection of a missing required param
(`cursor.next_cursor_path`), rejection of an unknown/typo'd param, the no-pagination default, and
round-trip stability through both YAML and JSON for `SourceConfig`. Pure model logic only — no
network, no fixtures carrying secrets or sample data (`steering/01-security.md`).
"""

from __future__ import annotations

import json

import pytest
import yaml
from pydantic import ValidationError

from dander.ingestion.pagination import (
    CursorPagination,
    HeaderCursorPagination,
    JsonLinkPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
    PaginationKind,
)
from dander.ingestion.source import Endpoint, SourceConfig


def test_each_kind_parses_with_its_params() -> None:
    assert NoPagination.model_validate({"kind": "none"}) == NoPagination()

    offset = OffsetPagination.model_validate(
        {"kind": "offset", "limit_param": "l", "offset_param": "o", "page_size": 50}
    )
    assert offset.limit_param == "l"
    assert offset.offset_param == "o"
    assert offset.page_size == 50

    cursor = CursorPagination.model_validate(
        {"kind": "cursor", "next_cursor_path": "meta.next_cursor", "cursor_param": "cursor_tok"}
    )
    assert cursor.next_cursor_path == "meta.next_cursor"
    assert cursor.cursor_param == "cursor_tok"

    page = PageNumberPagination.model_validate(
        {"kind": "page_number", "page_param": "p", "size_param": "sz", "start_page": 0}
    )
    assert page.page_param == "p"
    assert page.start_page == 0

    link = LinkHeaderPagination.model_validate(
        {"kind": "link_header", "header_name": "X-Link", "rel": "nxt"}
    )
    assert link.header_name == "X-Link"
    assert link.rel == "nxt"

    json_link = JsonLinkPagination.model_validate(
        {"kind": "json_link", "next_url_path": "paging.nextRecordsUrl"}
    )
    assert json_link.next_url_path == "paging.nextRecordsUrl"

    header_cursor = HeaderCursorPagination.model_validate(
        {
            "kind": "header_cursor",
            "next_cursor_header": "Sforce-Locator",
            "page_size": 5000,
        }
    )
    assert header_cursor.next_cursor_header == "Sforce-Locator"
    assert header_cursor.page_size == 5000


_BareCoercionTarget = (
    NoPagination
    | OffsetPagination
    | PageNumberPagination
    | LinkHeaderPagination
    | JsonLinkPagination
)


@pytest.mark.parametrize(
    ("bare_kind", "expected_type"),
    [
        ("none", NoPagination),
        ("offset", OffsetPagination),
        ("page_number", PageNumberPagination),
        ("link_header", LinkHeaderPagination),
        ("json_link", JsonLinkPagination),
    ],
)
def test_bare_string_coercion_on_endpoint(
    bare_kind: str, expected_type: type[_BareCoercionTarget]
) -> None:
    endpoint = Endpoint.model_validate(
        {"name": "widgets", "path": "/widgets", "pagination": bare_kind}
    )
    assert isinstance(endpoint.pagination, expected_type)
    assert endpoint.pagination.kind == PaginationKind(bare_kind)


def test_invalid_kind_rejected_bare_and_object() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate({"name": "widgets", "path": "/widgets", "pagination": "keyset"})

    with pytest.raises(ValidationError):
        Endpoint.model_validate(
            {"name": "widgets", "path": "/widgets", "pagination": {"kind": "keyset"}}
        )


def test_missing_required_param_rejected() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate(
            {"name": "widgets", "path": "/widgets", "pagination": {"kind": "cursor"}}
        )


def test_unknown_param_rejected() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate(
            {
                "name": "widgets",
                "path": "/widgets",
                "pagination": {"kind": "offset", "page_sixe": 10},
            }
        )


def test_no_pagination_is_the_default() -> None:
    endpoint = Endpoint(name="widgets", path="/widgets")
    assert endpoint.pagination == NoPagination()


def _sample_source_config() -> SourceConfig:
    return SourceConfig(
        name="example",
        base_url="https://example.test/v1",
        auth_strategy="api_key_basic",
        auth_ref="env:EXAMPLE_API_KEY",
        endpoints=[
            Endpoint(name="none_ep", path="/none", pagination="none"),
            Endpoint(
                name="offset_ep",
                path="/offset",
                pagination=OffsetPagination(
                    limit_param="limit", offset_param="offset", page_size=25
                ),
            ),
            Endpoint(
                name="cursor_ep",
                path="/cursor",
                pagination=CursorPagination(next_cursor_path="meta.next_cursor"),
            ),
            Endpoint(
                name="page_ep",
                path="/page",
                pagination=PageNumberPagination(page_param="page", size_param="per_page"),
            ),
            Endpoint(name="link_ep", path="/link", pagination="link_header"),
            Endpoint(
                name="json_link_ep",
                path="/json-link",
                pagination=JsonLinkPagination(next_url_path="nextRecordsUrl"),
            ),
            Endpoint(
                name="header_cursor_ep",
                path="/header-cursor",
                pagination=HeaderCursorPagination(next_cursor_header="Sforce-Locator"),
            ),
        ],
    )


def test_round_trip_stability_through_yaml() -> None:
    cfg = _sample_source_config()
    dumped = yaml.safe_dump(cfg.model_dump(by_alias=True, mode="json"))
    reloaded = SourceConfig.model_validate(yaml.safe_load(dumped))
    assert reloaded == cfg


def test_round_trip_stability_through_json() -> None:
    cfg = _sample_source_config()
    dumped = cfg.model_dump_json(by_alias=True)
    reloaded = SourceConfig.model_validate_json(dumped)
    assert reloaded == cfg
    # model_dump(mode="json") + json.loads should agree with model_dump_json.
    assert json.loads(dumped) == cfg.model_dump(by_alias=True, mode="json")


def test_dumped_pagination_is_always_object_form() -> None:
    cfg = _sample_source_config()
    dumped = cfg.model_dump(by_alias=True, mode="json")
    none_ep = next(e for e in dumped["endpoints"] if e["name"] == "none_ep")
    assert none_ep["pagination"] == {"kind": "none"}


def test_greenhouse_connector_yaml_still_loads() -> None:
    from pathlib import Path

    connector_path = Path(__file__).parents[2] / "connectors" / "greenhouse.yaml"
    cfg = SourceConfig.model_validate(yaml.safe_load(connector_path.read_text()))

    candidates = next(e for e in cfg.endpoints if e.name == "candidates")
    jobs = next(e for e in cfg.endpoints if e.name == "jobs")
    assert candidates.pagination == LinkHeaderPagination()
    assert jobs.pagination == LinkHeaderPagination()
