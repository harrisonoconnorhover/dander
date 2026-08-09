"""Stateful AWS Glue catalog publication and preservation tests."""

# The fake intentionally matches boto3's public keyword names.
# ruff: noqa: N803

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dander.catalog import CatalogPublishError, MetadataSpine
from dander.catalog.glue import GlueCatalogPublisher
from dander.transform import TransformProject
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.catalog import CatalogAsset

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


class _EntityNotFoundError(RuntimeError):
    """Minimal botocore-shaped not-found response."""

    response = {"Error": {"Code": "EntityNotFoundException"}}


class _FakeGlue:
    def __init__(self) -> None:
        self.databases: dict[str, dict[str, object]] = {}
        self.tables: dict[tuple[str, str], dict[str, object]] = {}
        self.calls: list[str] = []

    def get_database(self, *, CatalogId: str, Name: str) -> Mapping[str, object]:  # noqa: N803
        assert CatalogId == "123456789012"
        self.calls.append("get_database")
        try:
            return {"Database": deepcopy(self.databases[Name])}
        except KeyError as error:
            raise _EntityNotFoundError from error

    def create_database(  # noqa: N803
        self, *, CatalogId: str, DatabaseInput: Mapping[str, object]
    ) -> Mapping[str, object]:
        assert CatalogId == "123456789012"
        self.calls.append("create_database")
        self.databases[str(DatabaseInput["Name"])] = dict(deepcopy(DatabaseInput))
        return {}

    def update_database(  # noqa: N803
        self, *, CatalogId: str, Name: str, DatabaseInput: Mapping[str, object]
    ) -> Mapping[str, object]:
        assert CatalogId == "123456789012"
        self.calls.append("update_database")
        self.databases[Name] = dict(deepcopy(DatabaseInput))
        return {}

    def get_table(  # noqa: N803
        self, *, CatalogId: str, DatabaseName: str, Name: str
    ) -> Mapping[str, object]:
        assert CatalogId == "123456789012"
        self.calls.append("get_table")
        try:
            return {"Table": deepcopy(self.tables[(DatabaseName, Name)])}
        except KeyError as error:
            raise _EntityNotFoundError from error

    def create_table(  # noqa: N803
        self,
        *,
        CatalogId: str,
        DatabaseName: str,
        TableInput: Mapping[str, object],
    ) -> Mapping[str, object]:
        assert CatalogId == "123456789012"
        self.calls.append("create_table")
        self.tables[(DatabaseName, str(TableInput["Name"]))] = dict(deepcopy(TableInput))
        return {}

    def update_table(  # noqa: N803
        self,
        *,
        CatalogId: str,
        DatabaseName: str,
        Name: str,
        TableInput: Mapping[str, object],
        SkipArchive: bool,
    ) -> Mapping[str, object]:
        assert CatalogId == "123456789012"
        assert SkipArchive is False
        self.calls.append("update_table")
        self.tables[(DatabaseName, Name)] = dict(deepcopy(TableInput))
        return {}


def _asset() -> CatalogAsset:
    project = TransformProject.load(_MODELS_DIR, project_id="analytics")
    (asset,) = MetadataSpine().compile(project, selected=["stg_greenhouse__jobs"])
    return asset


def _publisher(client: _FakeGlue) -> GlueCatalogPublisher:
    return GlueCatalogPublisher(
        region="us-east-1",
        catalog_id="123456789012",
        database_prefix="dander",
        warehouse_provider="redshift",
        connection_name="analytics-redshift",
        client=client,
    )


def test_publish_creates_database_table_and_normalized_readback() -> None:
    client = _FakeGlue()
    publisher = _publisher(client)
    asset = _asset()

    resource = publisher.publish(asset)
    normalized = publisher.normalized_table(asset)

    assert resource == (
        "arn:aws:glue:us-east-1:123456789012:table/dander_analytics_staging/stg_greenhouse__jobs"
    )
    assert client.calls == [
        "get_database",
        "create_database",
        "get_table",
        "create_table",
        "get_table",
    ]
    assert normalized["database"] == "dander_analytics_staging"
    assert normalized["table"] == "stg_greenhouse__jobs"
    parameters = normalized["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["classification"] == "dander"
    assert parameters["dander.relation"] == "analytics.staging.stg_greenhouse__jobs"
    assert parameters["dander.warehouse_provider"] == "redshift"
    assert parameters["dander.connection_name"] == "analytics-redshift"
    columns = normalized["columns"]
    assert isinstance(columns, list)
    assert columns[0]["name"] == "job_id"
    assert columns[0]["type"] == "string"
    assert columns[0]["parameters"] == {
        "dander.logical_type": "STRING",
        "dander.nullable": "false",
    }


def test_update_preserves_unrelated_table_database_storage_and_column_fields() -> None:
    client = _FakeGlue()
    publisher = _publisher(client)
    asset = _asset()
    database = publisher.database_name(asset)
    table = publisher.table_name(asset)
    client.databases[database] = {
        "Name": database,
        "Description": "operator description",
        "LocationUri": "s3://operator-owned/catalog/",
        "Parameters": {"operator.note": "keep"},
    }
    client.tables[(database, table)] = {
        "Name": table,
        "Description": "old",
        "Retention": 17,
        "Parameters": {"operator.note": "keep", "dander.owner": "old"},
        "StorageDescriptor": {
            "Location": "s3://operator-owned/table/",
            "SerdeInfo": {"SerializationLibrary": "operator.serde"},
            "Columns": [
                {
                    "Name": "job_id",
                    "Type": "string",
                    "Parameters": {"operator.column": "keep"},
                }
            ],
        },
    }

    publisher.publish(asset)

    assert client.databases[database]["Description"] == "operator description"
    assert client.databases[database]["LocationUri"] == "s3://operator-owned/catalog/"
    assert client.databases[database]["Parameters"] == {
        "operator.note": "keep",
        "dander.catalog": "analytics",
        "dander.namespace": "staging",
        "dander.managed": "true",
    }
    updated = client.tables[(database, table)]
    assert updated["Retention"] == 17
    updated_parameters = updated["Parameters"]
    assert isinstance(updated_parameters, dict)
    assert updated_parameters["operator.note"] == "keep"
    storage = updated["StorageDescriptor"]
    assert isinstance(storage, dict)
    assert storage["Location"] == "s3://operator-owned/table/"
    assert storage["SerdeInfo"] == {"SerializationLibrary": "operator.serde"}
    assert storage["Columns"][0]["Parameters"]["operator.column"] == "keep"
    assert client.calls[-1] == "update_table"


def test_provider_errors_are_sanitized() -> None:
    class _BrokenGlue(_FakeGlue):
        def create_table(  # noqa: N803
            self,
            *,
            CatalogId: str,
            DatabaseName: str,
            TableInput: Mapping[str, object],
        ) -> Mapping[str, object]:
            del CatalogId, DatabaseName, TableInput
            raise RuntimeError("secret-bearing provider detail")

    with pytest.raises(CatalogPublishError, match="Cannot publish Glue catalog asset") as raised:
        _publisher(_BrokenGlue()).publish(_asset())

    assert "secret-bearing" not in str(raised.value)


def test_lossy_hive_name_normalization_gets_stable_coordinate_digest() -> None:
    publisher = _publisher(_FakeGlue())
    asset = replace(
        _asset(),
        relation_ref=RelationRef(
            catalog="Analytics-Prod",
            namespace="SalesOps",
            name="DailyRevenue",
        ),
    )

    assert publisher.database_name(asset) == "dander_analytics_prod_salesops_5e931afe4c"
    assert publisher.table_name(asset) == "dailyrevenue_52c237dcdf"
