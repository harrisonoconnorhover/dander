"""Ingestion module: source config models and the two extraction paths (dlt + hand-rolled)."""

from __future__ import annotations

from dander.ingestion.config import ConnectorConfigError, load_source_config
from dander.ingestion.dlt_backed import DltRestSource
from dander.ingestion.enterprise import (
    EnterpriseHttpClient,
    EnterpriseSource,
    EnterpriseSourceError,
    NetSuiteSuiteQLSource,
    OdooJson2Source,
    WorkdayRaasSource,
)
from dander.ingestion.pagination import (
    CursorPagination,
    JsonLinkPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
    PaginationKind,
    PaginationStrategy,
)
from dander.ingestion.source import Endpoint, IngestionEngine, RawField, Source, SourceConfig

__all__ = [
    "CursorPagination",
    "ConnectorConfigError",
    "DltRestSource",
    "Endpoint",
    "EnterpriseHttpClient",
    "EnterpriseSource",
    "EnterpriseSourceError",
    "IngestionEngine",
    "JsonLinkPagination",
    "LinkHeaderPagination",
    "NoPagination",
    "NetSuiteSuiteQLSource",
    "OffsetPagination",
    "OdooJson2Source",
    "PageNumberPagination",
    "PaginationKind",
    "PaginationStrategy",
    "RawField",
    "Source",
    "SourceConfig",
    "WorkdayRaasSource",
    "load_source_config",
]
