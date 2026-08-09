"""Dataplex aspect generation and safe request construction tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dander.catalog import (
    CatalogColumn,
    CatalogPublishError,
    DataplexAspectGenerator,
    DataplexCatalogPublisher,
    MetadataSpine,
)
from dander.transform import TransformProject
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from google.cloud import dataplex_v1

    from dander.catalog import CatalogAsset

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


class _FakeCatalogClient:
    def __init__(self) -> None:
        self.requests: list[dataplex_v1.ModifyEntryRequest] = []
        self.entries: dict[str, dataplex_v1.Entry] = {}

    def modify_entry(self, request: dataplex_v1.ModifyEntryRequest) -> object:
        self.requests.append(request)
        self.entries[request.entry.name] = request.entry
        return object()

    def get_entry(self, request: dataplex_v1.GetEntryRequest) -> dataplex_v1.Entry:
        return self.entries[request.name]


def _asset() -> CatalogAsset:
    project = TransformProject.load(_MODELS_DIR, project_id="valid-project-123")
    (asset,) = MetadataSpine().compile(project, selected=["stg_greenhouse__jobs"])
    return asset


def test_generator_uses_reusable_system_aspects_from_same_asset() -> None:
    generated = {aspect.key: aspect for aspect in DataplexAspectGenerator().generate(_asset())}

    assert set(generated) == {
        "dataplex-types.global.contacts",
        "dataplex-types.global.generic",
        "dataplex-types.global.overview",
        "dataplex-types.global.schema",
    }
    overview = generated["dataplex-types.global.overview"].data["content"]
    assert isinstance(overview, str)
    assert "Sensitivity:</strong> public" in overview
    schema = generated["dataplex-types.global.schema"].data["fields"]
    assert isinstance(schema, list)
    assert schema[0] == {
        "name": "job_id",
        "dataType": "STRING",
        "metadataType": "STRING",
        "mode": "REQUIRED",
        "description": "Stable Greenhouse public job identifier.",
    }
    assert schema[1]["mode"] == "NULLABLE"


def test_generator_escapes_overview_and_maps_bigquery_types_to_system_enum() -> None:
    asset = replace(
        _asset(),
        description="<script>unsafe</script>",
        columns=(
            CatalogColumn(
                name="amount",
                data_type="NUMERIC",
                description="Fixture amount.",
                nullable=True,
            ),
            CatalogColumn(
                name="location",
                data_type="GEOGRAPHY",
                description="Fixture location.",
                nullable=True,
            ),
        ),
    )
    generated = {aspect.key: aspect for aspect in DataplexAspectGenerator().generate(asset)}

    overview = generated["dataplex-types.global.overview"].data["content"]
    assert isinstance(overview, str)
    assert "<script>" not in overview
    assert "&lt;script&gt;" in overview
    schema = generated["dataplex-types.global.schema"].data["fields"]
    assert isinstance(schema, list)
    assert [field["metadataType"] for field in schema] == ["NUMBER", "GEOSPATIAL"]


def test_publisher_targets_bigquery_system_entry_and_only_generated_aspects() -> None:
    client = _FakeCatalogClient()
    publisher = DataplexCatalogPublisher(
        project="valid-project-123",
        location="us",
        client=client,
    )

    resource = publisher.publish(_asset())

    assert resource == (
        "projects/valid-project-123/locations/us/entryGroups/@bigquery/entries/"
        "bigquery.googleapis.com/projects/valid-project-123/datasets/staging/"
        "tables/stg_greenhouse__jobs"
    )
    (request,) = client.requests
    assert request.name == "projects/valid-project-123/locations/us"
    assert request.update_mask.paths == ["aspects"]
    assert request.delete_missing_aspects is False
    assert set(request.aspect_keys) == {
        "dataplex-types.global.contacts",
        "dataplex-types.global.generic",
        "dataplex-types.global.overview",
    }
    assert set(request.aspect_keys) == set(request.entry.aspects)
    assert "dataplex-types.global.schema" not in request.entry.aspects
    generic = request.entry.aspects["dataplex-types.global.generic"].data
    assert generic["type"] == "dander-view"
    assert generic["system"] == "greenhouse_job_board"


def test_publisher_reads_back_normalized_aspects() -> None:
    client = _FakeCatalogClient()
    publisher = DataplexCatalogPublisher(
        project="valid-project-123",
        location="us",
        client=client,
    )
    asset = _asset()
    publisher.publish(asset)

    normalized = publisher.normalized_aspects(asset)

    assert set(normalized) == {
        "dataplex-types.global.contacts",
        "dataplex-types.global.generic",
        "dataplex-types.global.overview",
    }


def test_request_rejects_asset_from_different_project() -> None:
    publisher = DataplexCatalogPublisher(
        project="valid-project-123",
        location="us",
        client=_FakeCatalogClient(),
    )

    with pytest.raises(CatalogPublishError, match="different project"):
        publisher.request_for(
            replace(
                _asset(),
                relation_ref=RelationRef(
                    catalog="other-project-123",
                    namespace="staging",
                    name="stg_greenhouse__jobs",
                ),
            )
        )


def test_location_is_validated_before_client_creation() -> None:
    with pytest.raises(CatalogPublishError, match="Invalid Dataplex location"):
        DataplexCatalogPublisher(
            project="valid-project-123",
            location="../unsafe",
            client=_FakeCatalogClient(),
        )
