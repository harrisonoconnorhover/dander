"""AWS Glue Data Catalog publication from canonical Dander metadata."""

# boto3 method keyword names are part of AWS's public request contract.
# ruff: noqa: N803

from __future__ import annotations

import hashlib
import json
import re
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, cast

from dander.catalog.publisher import CatalogPublishError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.catalog.spine import CatalogAsset, CatalogColumn

_DANDER_PARAMETER_PREFIX = "dander."
_INPUT_TABLE_FIELDS = frozenset(
    {
        "LastAccessTime",
        "LastAnalyzedTime",
        "Retention",
        "PartitionKeys",
        "ViewOriginalText",
        "ViewExpandedText",
        "TargetTable",
        "ViewDefinition",
    }
)
_INPUT_STORAGE_FIELDS = frozenset(
    {
        "Location",
        "AdditionalLocations",
        "InputFormat",
        "OutputFormat",
        "Compressed",
        "NumberOfBuckets",
        "SerdeInfo",
        "BucketColumns",
        "SortColumns",
        "Parameters",
        "SkewedInfo",
        "StoredAsSubDirectories",
        "SchemaReference",
    }
)
_NON_NAME = re.compile(r"[^a-z0-9_]+")


class GlueClient(Protocol):
    """Small subset of the boto3 Glue client used by Dander."""

    def get_database(self, *, CatalogId: str, Name: str) -> Mapping[str, object]:  # noqa: N803
        """Read one database."""

    def create_database(  # noqa: N803
        self, *, CatalogId: str, DatabaseInput: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Create one database."""

    def update_database(  # noqa: N803
        self, *, CatalogId: str, Name: str, DatabaseInput: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Update one database."""

    def get_table(  # noqa: N803
        self, *, CatalogId: str, DatabaseName: str, Name: str
    ) -> Mapping[str, object]:
        """Read one table."""

    def create_table(  # noqa: N803
        self,
        *,
        CatalogId: str,
        DatabaseName: str,
        TableInput: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Create one table."""

    def update_table(  # noqa: N803
        self,
        *,
        CatalogId: str,
        DatabaseName: str,
        Name: str,
        TableInput: Mapping[str, object],
        SkipArchive: bool,
    ) -> Mapping[str, object]:
        """Update one table."""


class GlueCatalogPublisher:
    """Create or update Dander-owned Glue fields without deleting unrelated metadata."""

    def __init__(
        self,
        *,
        region: str,
        catalog_id: str,
        database_prefix: str,
        warehouse_provider: str,
        connection_name: str | None = None,
        client: GlueClient | None = None,
    ) -> None:
        self._region = region
        self._catalog_id = catalog_id
        self._database_prefix = database_prefix
        self._warehouse_provider = warehouse_provider
        self._connection_name = connection_name
        self._client = client

    def publish(self, asset: CatalogAsset) -> str:
        """Upsert one canonical asset and return its stable Glue ARN."""
        database_name = self.database_name(asset)
        table_name = self.table_name(asset)
        self._ensure_database(asset, database_name)
        try:
            response = self._glue_client().get_table(
                CatalogId=self._catalog_id,
                DatabaseName=database_name,
                Name=table_name,
            )
            existing = _mapping(response.get("Table"))
        except Exception as error:
            if not _is_not_found(error):
                raise CatalogPublishError(
                    f"Cannot read Glue catalog asset: {asset.name}"
                ) from error
            existing = None

        table_input = self.table_input_for(asset, existing=existing)
        try:
            if existing is None:
                self._glue_client().create_table(
                    CatalogId=self._catalog_id,
                    DatabaseName=database_name,
                    TableInput=table_input,
                )
            else:
                self._glue_client().update_table(
                    CatalogId=self._catalog_id,
                    DatabaseName=database_name,
                    Name=table_name,
                    TableInput=table_input,
                    SkipArchive=False,
                )
        except Exception as error:
            raise CatalogPublishError(f"Cannot publish Glue catalog asset: {asset.name}") from error
        return f"arn:aws:glue:{self._region}:{self._catalog_id}:table/{database_name}/{table_name}"

    def read(self, asset: CatalogAsset) -> Mapping[str, object]:
        """Read back one published Glue table."""
        try:
            response = self._glue_client().get_table(
                CatalogId=self._catalog_id,
                DatabaseName=self.database_name(asset),
                Name=self.table_name(asset),
            )
        except Exception as error:
            raise CatalogPublishError(f"Cannot read Glue catalog asset: {asset.name}") from error
        table = _mapping(response.get("Table"))
        if table is None:
            raise CatalogPublishError(f"Glue catalog asset is malformed: {asset.name}")
        return table

    def normalized_table(self, asset: CatalogAsset) -> dict[str, object]:
        """Return Dander-owned readback fields in deterministic provider-neutral form."""
        table = self.read(asset)
        storage = _mapping(table.get("StorageDescriptor")) or {}
        raw_columns = storage.get("Columns")
        columns = raw_columns if isinstance(raw_columns, list) else []
        parameters = _string_mapping(table.get("Parameters"))
        return {
            "database": self.database_name(asset),
            "table": str(table.get("Name", "")),
            "description": str(table.get("Description", "")),
            "owner": str(table.get("Owner", "")),
            "columns": [_normalize_column(item) for item in columns if isinstance(item, dict)],
            "parameters": {
                key: value
                for key, value in sorted(parameters.items())
                if key == "classification" or key.startswith(_DANDER_PARAMETER_PREFIX)
            },
        }

    def database_name(self, asset: CatalogAsset) -> str:
        """Map catalog and namespace into one collision-safe Glue database name."""
        coordinate = f"{asset.relation_ref.catalog}.{asset.relation_ref.namespace}"
        raw = f"{self._database_prefix}_{asset.relation_ref.catalog}_{asset.relation_ref.namespace}"
        return _glue_name(raw, identity=coordinate)

    def table_name(self, asset: CatalogAsset) -> str:
        """Map one relation name into Hive-compatible lowercase form."""
        return _glue_name(asset.relation_ref.name, identity=asset.relation)

    def table_input_for(
        self,
        asset: CatalogAsset,
        *,
        existing: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Build an update-safe TableInput while retaining unrelated provider fields."""
        existing = existing or {}
        table_input = {key: value for key, value in existing.items() if key in _INPUT_TABLE_FIELDS}
        existing_storage = _mapping(existing.get("StorageDescriptor")) or {}
        storage = {
            key: value for key, value in existing_storage.items() if key in _INPUT_STORAGE_FIELDS
        }
        existing_columns = _columns_by_name(existing_storage.get("Columns"))
        storage["Columns"] = [
            _column_input(column, existing=existing_columns.get(column.name))
            for column in asset.columns
        ]

        parameters = _string_mapping(existing.get("Parameters"))
        parameters.update(self._owned_parameters(asset))
        table_input.update(
            {
                "Name": self.table_name(asset),
                "Description": _utf8_prefix(asset.description, 2048),
                "Owner": _utf8_prefix(asset.owner, 255),
                "StorageDescriptor": storage,
                "TableType": "EXTERNAL_TABLE",
                "Parameters": parameters,
            }
        )
        return table_input

    def _ensure_database(self, asset: CatalogAsset, database_name: str) -> None:
        try:
            response = self._glue_client().get_database(
                CatalogId=self._catalog_id,
                Name=database_name,
            )
            existing = _mapping(response.get("Database"))
        except Exception as error:
            if not _is_not_found(error):
                raise CatalogPublishError(
                    f"Cannot read Glue database for catalog asset: {asset.name}"
                ) from error
            existing = None

        parameters = _string_mapping(existing.get("Parameters") if existing else None)
        desired_parameters = parameters | {
            "dander.catalog": asset.relation_ref.catalog,
            "dander.namespace": asset.relation_ref.namespace,
            "dander.managed": "true",
        }
        database_input: dict[str, object] = {
            "Name": database_name,
            "Parameters": desired_parameters,
        }
        try:
            if existing is None:
                database_input["Description"] = (
                    "Dander catalog projection for "
                    f"{asset.relation_ref.catalog}.{asset.relation_ref.namespace}"
                )
                self._glue_client().create_database(
                    CatalogId=self._catalog_id,
                    DatabaseInput=database_input,
                )
            elif desired_parameters != parameters:
                for key in (
                    "Description",
                    "LocationUri",
                    "CreateTableDefaultPermissions",
                    "TargetDatabase",
                    "FederatedDatabase",
                ):
                    if key in existing:
                        database_input[key] = existing[key]
                self._glue_client().update_database(
                    CatalogId=self._catalog_id,
                    Name=database_name,
                    DatabaseInput=database_input,
                )
        except Exception as error:
            raise CatalogPublishError(
                f"Cannot prepare Glue database for catalog asset: {asset.name}"
            ) from error

    def _owned_parameters(self, asset: CatalogAsset) -> dict[str, str]:
        manifest = asset.to_manifest()
        parameters = {
            "classification": "dander",
            "dander.catalog": asset.relation_ref.catalog,
            "dander.namespace": asset.relation_ref.namespace,
            "dander.relation": asset.relation,
            "dander.warehouse_provider": self._warehouse_provider,
            "dander.materialization": asset.materialization,
            "dander.source_system": asset.source_system,
            "dander.sensitivity": asset.sensitivity,
            "dander.owner": asset.owner,
            "dander.lineage": _json(list(asset.upstream_relations)),
            "dander.tests": _json([test.to_manifest() for test in asset.tests]),
            "dander.metrics": _json([metric.to_manifest() for metric in asset.metrics]),
            "dander.manifest_sha256": hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest(),
        }
        if self._connection_name is not None:
            parameters["dander.connection_name"] = self._connection_name
        return {key: _utf8_prefix(value, 512_000) for key, value in parameters.items()}

    def _glue_client(self) -> GlueClient:
        if self._client is None:
            boto3 = cast("Any", import_module("boto3"))
            self._client = cast(
                "GlueClient",
                boto3.client("glue", region_name=self._region),
            )
        return self._client


def _column_input(
    column: CatalogColumn,
    *,
    existing: Mapping[str, object] | None,
) -> dict[str, object]:
    parameters = _string_mapping(existing.get("Parameters") if existing else None)
    parameters.update(
        {
            "dander.logical_type": column.data_type,
            "dander.nullable": str(column.nullable).lower(),
        }
    )
    return {
        "Name": column.name,
        "Type": _glue_type(column.data_type),
        "Comment": _utf8_prefix(column.description, 255),
        "Parameters": parameters,
    }


def _glue_type(data_type: str) -> str:
    normalized = data_type.strip().upper()
    if normalized.startswith("DECIMAL("):
        return normalized.lower()
    return {
        "BOOLEAN": "boolean",
        "BOOL": "boolean",
        "SMALLINT": "smallint",
        "INTEGER": "bigint",
        "INT64": "bigint",
        "BIGINT": "bigint",
        "FLOAT": "double",
        "FLOAT64": "double",
        "DOUBLE": "double",
        "NUMERIC": "decimal(38,9)",
        "BIGNUMERIC": "decimal(38,9)",
        "DECIMAL": "decimal(38,9)",
        "STRING": "string",
        "TEXT": "string",
        "VARCHAR": "string",
        "BYTES": "binary",
        "BINARY": "binary",
        "DATE": "date",
        "DATETIME": "timestamp",
        "TIMESTAMP": "timestamp",
        "TIMESTAMPTZ": "timestamp",
        "TIME": "string",
        "JSON": "string",
        "JSONB": "string",
        "VARIANT": "string",
        "SUPER": "string",
        "RECORD": "string",
        "STRUCT": "string",
    }.get(normalized, "string")


def _glue_name(value: str, *, identity: str) -> str:
    lowered = value.lower()
    normalized = _NON_NAME.sub("_", lowered).strip("_") or "relation"
    if normalized != value or len(normalized.encode("utf-8")) > 255:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized[:244].rstrip('_')}_{digest}"
    return normalized


def _columns_by_name(value: object) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["Name"]): item
        for item in value
        if isinstance(item, dict) and isinstance(item.get("Name"), str)
    }


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _normalize_column(value: Mapping[str, object]) -> dict[str, object]:
    parameters = _string_mapping(value.get("Parameters"))
    return {
        "name": str(value.get("Name", "")),
        "type": str(value.get("Type", "")),
        "comment": str(value.get("Comment", "")),
        "parameters": {
            key: item
            for key, item in sorted(parameters.items())
            if key.startswith(_DANDER_PARAMETER_PREFIX)
        },
    }


def _is_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        detail = response.get("Error")
        if isinstance(detail, dict) and detail.get("Code") == "EntityNotFoundException":
            return True
    return type(error).__name__ == "EntityNotFoundException"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _utf8_prefix(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


__all__ = ["GlueCatalogPublisher", "GlueClient"]
