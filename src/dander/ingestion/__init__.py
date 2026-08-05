"""Ingestion module: source config models and the two extraction paths (dlt + hand-rolled)."""

from __future__ import annotations

from dander.ingestion.capabilities import (
    RECORD_NOT_FOUND,
    ConnectionStatus,
    ConnectorOperation,
    CountPrecision,
    CountResult,
    InvalidConnectorCapabilityResultError,
    RecordNotFound,
    SourceCapabilities,
    SupportsCount,
    SupportsGetSingleObject,
    SupportsTestConnection,
    UnsupportedConnectorOperationError,
)
from dander.ingestion.config import ConnectorConfigError, load_source_config
from dander.ingestion.dlt_backed import DltRestSource
from dander.ingestion.enterprise import (
    EnterpriseHttpClient,
    EnterpriseSource,
    EnterpriseSourceError,
    NetSuiteSuiteQLSource,
    OdooJson2Source,
    SalesforceBulk2Source,
    WorkdayRaasSource,
)
from dander.ingestion.pagination import (
    CursorPagination,
    HeaderCursorPagination,
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
    "RECORD_NOT_FOUND",
    "ConnectionStatus",
    "CursorPagination",
    "ConnectorConfigError",
    "ConnectorOperation",
    "CountPrecision",
    "CountResult",
    "DltRestSource",
    "Endpoint",
    "EnterpriseHttpClient",
    "EnterpriseSource",
    "EnterpriseSourceError",
    "HeaderCursorPagination",
    "IngestionEngine",
    "InvalidConnectorCapabilityResultError",
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
    "RecordNotFound",
    "SalesforceBulk2Source",
    "Source",
    "SourceCapabilities",
    "SourceConfig",
    "SupportsCount",
    "SupportsGetSingleObject",
    "SupportsTestConnection",
    "UnsupportedConnectorOperationError",
    "WorkdayRaasSource",
    "load_source_config",
]
