"""Dataplex Knowledge Catalog aspect generation and publication."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import dataplex_v1

from dander.catalog.publisher import CatalogPublishError

if TYPE_CHECKING:
    from dander.catalog.spine import CatalogAsset

_LOCATION = re.compile(r"^[a-z0-9-]+$")
_SYSTEM_ASPECT_PROJECT = "projects/dataplex-types/locations/global/aspectTypes"
_OVERVIEW = "dataplex-types.global.overview"
_CONTACTS = "dataplex-types.global.contacts"
_SCHEMA = "dataplex-types.global.schema"
_GENERIC = "dataplex-types.global.generic"
_BIGQUERY_MANAGED_REQUIRED_ASPECTS = frozenset({_SCHEMA})


class _CatalogClient(Protocol):
    def modify_entry(self, request: dataplex_v1.ModifyEntryRequest) -> object:
        """Modify a first-party catalog entry through source-system permissions."""

    def get_entry(self, request: dataplex_v1.GetEntryRequest) -> dataplex_v1.Entry:
        """Read one first-party catalog entry."""


@dataclass(frozen=True)
class GeneratedAspect:
    """One reusable Dataplex system aspect and its JSON-compatible data."""

    key: str
    aspect_type: str
    data: dict[str, object]


class DataplexAspectGenerator:
    """Generate reusable system aspects from a cloud-neutral catalog asset."""

    def generate(self, asset: CatalogAsset) -> tuple[GeneratedAspect, ...]:
        """Generate overview, owner, schema, and source-system aspects."""
        upstream = ", ".join(asset.upstream_relations) or "none"
        overview = (
            f"<p>{html.escape(asset.description)}</p>"
            f"<p><strong>Sensitivity:</strong> {html.escape(asset.sensitivity)}</p>"
            f"<p><strong>Upstream:</strong> {html.escape(upstream)}</p>"
        )
        schema_fields = [
            {
                "name": column.name,
                "dataType": column.data_type,
                "metadataType": _metadata_type(column.data_type),
                "mode": "NULLABLE" if column.nullable else "REQUIRED",
                "description": column.description,
            }
            for column in asset.columns
        ]
        return (
            GeneratedAspect(
                key=_OVERVIEW,
                aspect_type=f"{_SYSTEM_ASPECT_PROJECT}/overview",
                data={"content": overview, "links": []},
            ),
            GeneratedAspect(
                key=_CONTACTS,
                aspect_type=f"{_SYSTEM_ASPECT_PROJECT}/contacts",
                data={"identities": [{"role": "owner", "name": asset.owner, "id": asset.owner}]},
            ),
            GeneratedAspect(
                key=_SCHEMA,
                aspect_type=f"{_SYSTEM_ASPECT_PROJECT}/schema",
                data={"fields": schema_fields},
            ),
            GeneratedAspect(
                key=_GENERIC,
                aspect_type=f"{_SYSTEM_ASPECT_PROJECT}/generic",
                data={
                    "type": f"dander-{asset.materialization}",
                    "system": asset.source_system,
                },
            ),
        )


class DataplexCatalogPublisher:
    """Attach optional generated aspects to first-party BigQuery catalog entries."""

    def __init__(
        self,
        *,
        project: str,
        location: str,
        client: _CatalogClient | None = None,
        generator: DataplexAspectGenerator | None = None,
    ) -> None:
        if not _LOCATION.fullmatch(location):
            raise CatalogPublishError("Invalid Dataplex location")
        self._project = project
        self._location = location
        self._client = client or cast("_CatalogClient", dataplex_v1.CatalogServiceClient())
        self._generator = generator or DataplexAspectGenerator()

    def request_for(self, asset: CatalogAsset) -> dataplex_v1.ModifyEntryRequest:
        """Build an aspect-only request that preserves all unrelated aspects."""
        if asset.project != self._project:
            raise CatalogPublishError("Catalog asset belongs to a different project")
        aspects = self._publishable_aspects(asset)
        entry_name = _bigquery_entry_name(asset, location=self._location)
        entry = dataplex_v1.Entry(
            name=entry_name,
            aspects={
                aspect.key: dataplex_v1.Aspect(
                    aspect_type=aspect.aspect_type,
                    data=aspect.data,
                )
                for aspect in aspects
            },
        )
        return dataplex_v1.ModifyEntryRequest(
            name=f"projects/{self._project}/locations/{self._location}",
            entry=entry,
            update_mask={"paths": ["aspects"]},
            aspect_keys=[aspect.key for aspect in aspects],
            delete_missing_aspects=False,
        )

    def publish(self, asset: CatalogAsset) -> str:
        """Publish generated aspects and return the modified entry resource name."""
        request = self.request_for(asset)
        try:
            self._client.modify_entry(request=request)
        except GoogleAPICallError as error:
            raise CatalogPublishError(f"Cannot publish catalog asset: {asset.name}") from error
        return request.entry.name

    def read(self, asset: CatalogAsset) -> dataplex_v1.Entry:
        """Read back one entry without modifying unrelated aspects."""
        request = dataplex_v1.GetEntryRequest(
            name=_bigquery_entry_name(asset, location=self._location)
        )
        try:
            return self._client.get_entry(request=request)
        except GoogleAPICallError as error:
            raise CatalogPublishError(f"Cannot read catalog asset: {asset.name}") from error

    def normalized_aspects(self, asset: CatalogAsset) -> dict[str, object]:
        """Return a stable, JSON-compatible view of the generated aspects read from Dataplex."""
        entry = self.read(asset)
        return {
            key: _normalize_value(aspect.data)
            for key, aspect in sorted(entry.aspects.items())
            if key in {generated.key for generated in self._publishable_aspects(asset)}
        }

    def _publishable_aspects(self, asset: CatalogAsset) -> tuple[GeneratedAspect, ...]:
        """Exclude required aspects that Google manages for BigQuery system entries."""
        return tuple(
            aspect
            for aspect in self._generator.generate(asset)
            if aspect.key not in _BIGQUERY_MANAGED_REQUIRED_ASPECTS
        )


def _bigquery_entry_name(asset: CatalogAsset, *, location: str) -> str:
    entry_id = (
        f"bigquery.googleapis.com/projects/{asset.project}"
        f"/datasets/{asset.dataset}/tables/{asset.name}"
    )
    return f"projects/{asset.project}/locations/{location}/entryGroups/@bigquery/entries/{entry_id}"


def _normalize_value(value: object) -> object:
    """Normalize protobuf Struct-like values without retaining provider payload wrappers."""
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if hasattr(value, "items"):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items())}
    return value


def _metadata_type(data_type: str) -> str:
    normalized = data_type.upper()
    if normalized in {"BOOL", "BOOLEAN"}:
        return "BOOLEAN"
    if normalized in {
        "BIGNUMERIC",
        "DECIMAL",
        "FLOAT",
        "FLOAT64",
        "INT64",
        "INTEGER",
        "NUMERIC",
        "SMALLINT",
    }:
        return "NUMBER"
    if normalized == "STRING":
        return "STRING"
    if normalized in {"BINARY", "BYTES"}:
        return "BYTES"
    if normalized in {"DATE", "DATETIME", "TIME"}:
        return "DATETIME"
    if normalized == "TIMESTAMP":
        return "TIMESTAMP"
    if normalized in {"GEOGRAPHY", "GEOSPATIAL"}:
        return "GEOSPATIAL"
    if normalized in {"RECORD", "STRUCT"} or normalized.startswith("STRUCT<"):
        return "STRUCT"
    return "OTHER"
