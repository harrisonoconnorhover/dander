"""Pagination modeled as a typed strategy, not a free string.

`Endpoint.pagination` (see `dander.ingestion.source`) used to be a bare `str = "none"`: it could
not express the parameters each real pagination style needs and invited typo'd, unvalidated
values. This module mirrors the strategy pattern the codebase already uses for auth
(`AuthStrategy`, `steering/01-security.md`) and for the closed kind sets in `pipeline/graph.py`
(`TransformationKind`, `JoinType`): a closed, named `PaginationKind` plus one Pydantic model per
kind, each carrying exactly the parameters that style needs.

Unlike `Transformation` in `pipeline/graph.py` — a single model with a `model_validator` because
its kinds share an optional payload — pagination kinds have genuinely disjoint, individually
required parameters. So this is a **Pydantic v2 discriminated union** on `kind`: each kind gets
its own model with its own required, typed fields, and Pydantic rejects an out-of-set `kind` or a
missing required param at the parse boundary with a clear error, with no hand-rolled cross-field
validation needed.

These models are plain, inert config — no HTTP/paging is performed here (that is explicitly out of
scope for this ticket). The behavioral seam (a future `build_next_request` / `extract_next_cursor`)
is deliberately not built; no ticket asks for it and it would be speculative generality.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class PaginationKind(StrEnum):
    """The closed set of pagination styles a `PaginationStrategy` may declare.

    A `StrEnum` (matching the `TransformationKind`/`JoinType`/`HttpMethod` convention elsewhere in
    the codebase) so callers get a named, importable type that still serializes to/from its plain
    string value stably in YAML and JSON.

    Attributes:
        NONE: No pagination — a single request retrieves the full result set.
        OFFSET: Offset/limit-style pagination (``?offset=...&limit=...``).
        CURSOR: Opaque cursor/token pagination, where the next cursor is read out of the response
            body (e.g. ``meta.next_cursor``).
        PAGE_NUMBER: Page-number-style pagination (``?page=...&per_page=...``).
        LINK_HEADER: RFC 5988 ``Link`` response-header pagination (e.g. Greenhouse Harvest).
        JSON_LINK: Next-page URL read from the JSON response body (e.g. Salesforce Query).
        HEADER_CURSOR: Opaque next-page cursor returned in a response header (e.g. Salesforce
            Bulk API 2.0 query results).
    """

    NONE = "none"
    OFFSET = "offset"
    CURSOR = "cursor"
    PAGE_NUMBER = "page_number"
    LINK_HEADER = "link_header"
    JSON_LINK = "json_link"
    HEADER_CURSOR = "header_cursor"


class NoPagination(BaseModel):
    """Explicit "single request, no paging" strategy — the default.

    Attributes:
        kind: Always `PaginationKind.NONE`; the discriminator value for this strategy.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.NONE] = PaginationKind.NONE


class OffsetPagination(BaseModel):
    """Offset/limit-style pagination strategy.

    Attributes:
        kind: Always `PaginationKind.OFFSET`; the discriminator value for this strategy.
        limit_param: Query-param name carrying the page size. Defaults to ``"limit"``.
        offset_param: Query-param name carrying the starting offset. Defaults to ``"offset"``.
        page_size: Number of records requested per page. Defaults to `100` — a starting value,
            not a tuned one; no paging is exercised by this ticket.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.OFFSET] = PaginationKind.OFFSET
    limit_param: str = "limit"
    offset_param: str = "offset"
    page_size: int = Field(default=100, gt=0)


class CursorPagination(BaseModel):
    """Opaque cursor/token pagination strategy.

    Attributes:
        kind: Always `PaginationKind.CURSOR`; the discriminator value for this strategy.
        next_cursor_path: Dotted-path location of the next cursor within the response body (e.g.
            ``"meta.next_cursor"``). Required, with no sensible default — where a cursor lives in
            a response is genuinely source-specific, and this is the one pagination kind without
            a bare-string shorthand (`dander.ingestion.source.Endpoint`) as a result.
        cursor_param: Query-param name the next request sends the cursor back on. Defaults to
            ``"cursor"``.
        size_param: Query-param name carrying the page size, or `None` if the source has no such
            param.
        page_size: Number of records requested per page, or `None` to omit a page-size param
            entirely.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.CURSOR] = PaginationKind.CURSOR
    next_cursor_path: str
    cursor_param: str = "cursor"
    size_param: str | None = None
    page_size: int | None = Field(default=None, gt=0)


class PageNumberPagination(BaseModel):
    """Page-number-style pagination strategy.

    Attributes:
        kind: Always `PaginationKind.PAGE_NUMBER`; the discriminator value for this strategy.
        page_param: Query-param name carrying the page number. Defaults to ``"page"``.
        size_param: Query-param name carrying the page size. Defaults to ``"per_page"``.
        page_size: Number of records requested per page. Defaults to `100` — a starting value,
            not a tuned one; no paging is exercised by this ticket.
        start_page: The first page number a source expects. Defaults to `1`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.PAGE_NUMBER] = PaginationKind.PAGE_NUMBER
    page_param: str = "page"
    size_param: str = "per_page"
    page_size: int = Field(default=100, gt=0)
    start_page: int = Field(default=1, ge=0)


class LinkHeaderPagination(BaseModel):
    """RFC 5988 ``Link`` response-header pagination strategy (e.g. Greenhouse Harvest).

    Attributes:
        kind: Always `PaginationKind.LINK_HEADER`; the discriminator value for this strategy.
        header_name: Response header carrying the link relations. Defaults to ``"Link"``.
        rel: The ``rel`` value identifying the next-page link. Defaults to ``"next"``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.LINK_HEADER] = PaginationKind.LINK_HEADER
    header_name: str = "Link"
    rel: str = "next"


class JsonLinkPagination(BaseModel):
    """Next-page URL carried inside the JSON response body.

    Salesforce Query and QueryAll return a relative ``nextRecordsUrl`` rather than a cursor that
    must be copied into a query parameter. Treating that value as a URL keeps provider-owned query
    locators opaque and lets dlt resolve relative links against the response URL.

    Attributes:
        kind: Always `PaginationKind.JSON_LINK`; the discriminator for this strategy.
        next_url_path: Dotted path to the next-page URL. Defaults to ``"next"``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.JSON_LINK] = PaginationKind.JSON_LINK
    next_url_path: str = "next"


class HeaderCursorPagination(BaseModel):
    """Opaque cursor returned in a response header and echoed as a query parameter."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal[PaginationKind.HEADER_CURSOR] = PaginationKind.HEADER_CURSOR
    next_cursor_header: str
    cursor_param: str = "locator"
    size_param: str = "maxRecords"
    page_size: int = Field(default=10_000, gt=0, le=100_000)
    terminal_value: str = "null"


PaginationStrategy = Annotated[
    NoPagination
    | OffsetPagination
    | CursorPagination
    | PageNumberPagination
    | LinkHeaderPagination
    | JsonLinkPagination
    | HeaderCursorPagination,
    Field(discriminator="kind"),
]
"""The pagination-strategy type alias. Application code (and `Endpoint.pagination`) depends on
this alias, never a concrete kind — the discriminated union rejects an out-of-set `kind` or a
missing required param at the Pydantic parse boundary."""
