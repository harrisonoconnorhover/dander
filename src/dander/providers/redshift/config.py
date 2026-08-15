"""Validated Redshift warehouse settings containing no AWS credentials."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dander.warehouse import RelationRef

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,126}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}[A-Za-z0-9]$")
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_IAM_ROLE = re.compile(r"^arn:(?:aws|aws-us-gov):iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]+$")
_RESOURCE_NAME = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_DIRECT_LOGICAL_BYTES = 1_024 * 1_024


class RedshiftWarehouseConfig(BaseModel):
    """One provisioned or Serverless Redshift data-plane profile."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    provider: Literal["redshift"]
    deployment: Literal["provisioned", "serverless"]
    host: str
    port: int = Field(default=5439, ge=1, le=65_535)
    database: str
    schema_name: str = Field(default="raw", alias="schema")
    db_user: str | None = None
    database_role: str | None = None
    region: str
    cluster_identifier: str | None = None
    workgroup_name: str | None = None
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str = "dander/staging"
    connect_timeout_seconds: int = Field(default=30, ge=1, le=300)
    statement_timeout_ms: int = Field(default=900_000, ge=1_000, le=3_600_000)
    max_rows_per_file: int = Field(default=100_000, ge=1, le=1_000_000)
    max_logical_bytes_per_file: int = Field(
        default=128 * 1_024 * 1_024,
        ge=1_024,
        le=1_073_741_824,
    )
    compression: Literal["snappy", "zstd"] = "zstd"
    direct_max_rows: int = Field(default=0, ge=0, le=10_000)
    direct_max_logical_bytes: int = Field(
        default=0,
        ge=0,
        le=_MAX_DIRECT_LOGICAL_BYTES,
    )

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        if (self.direct_max_rows == 0) != (self.direct_max_logical_bytes == 0):
            raise ValueError(
                "direct_max_rows and direct_max_logical_bytes must both be zero or positive"
            )
        for field_name, value in (
            ("database", self.database),
            ("schema", self.schema_name),
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{field_name} must use portable lowercase identifier syntax")
        if self.db_user is not None and not _IDENTIFIER.fullmatch(self.db_user):
            raise ValueError("db_user must use portable lowercase identifier syntax")
        if self.database_role is not None and not _IDENTIFIER.fullmatch(self.database_role):
            raise ValueError("database_role must use portable lowercase identifier syntax")
        if not _HOST.fullmatch(self.host):
            raise ValueError("host must be a DNS name")
        if not _AWS_REGION.fullmatch(self.region):
            raise ValueError("region must be an AWS region")
        if not _IAM_ROLE.fullmatch(self.copy_role_arn):
            raise ValueError("copy_role_arn must reference one AWS IAM role")
        if not _S3_BUCKET.fullmatch(self.staging_bucket):
            raise ValueError("staging_bucket must be a valid S3 bucket name")
        prefix = self.staging_prefix.strip("/")
        if (
            not prefix
            or ".." in prefix.split("/")
            or any(char in "*?" or ord(char) < 32 for char in prefix)
        ):
            raise ValueError("staging_prefix must be a safe non-empty S3 key prefix")
        object.__setattr__(self, "staging_prefix", prefix)
        if self.deployment == "provisioned":
            if (
                not self.cluster_identifier
                or self.workgroup_name is not None
                or self.db_user is None
            ):
                raise ValueError(
                    "provisioned Redshift requires cluster_identifier and db_user only"
                )
            assert self.cluster_identifier is not None
            if not _RESOURCE_NAME.fullmatch(self.cluster_identifier):
                raise ValueError("cluster_identifier must use lowercase AWS resource syntax")
        elif (
            not self.workgroup_name
            or self.cluster_identifier is not None
            or self.db_user is not None
        ):
            raise ValueError("Redshift Serverless requires workgroup_name and an AWS-derived user")
        else:
            assert self.workgroup_name is not None
            if not _RESOURCE_NAME.fullmatch(self.workgroup_name):
                raise ValueError("workgroup_name must use lowercase AWS resource syntax")
        return self

    def raw_relation(
        self,
        name: str,
        *,
        compatibility_catalog: str | None,
        compatibility_namespace: str | None,
    ) -> RelationRef:
        """Translate compatibility inputs into Redshift database/schema coordinates."""
        del compatibility_catalog
        relation = RelationRef(
            catalog=self.database,
            namespace=compatibility_namespace or self.schema_name,
            name=name,
        )
        validate_redshift_relation(relation)
        return relation


def validate_redshift_relation(relation: RelationRef) -> None:
    """Reject identifiers Redshift would silently truncate."""
    if any(len(value.encode()) > 127 for value in (relation.namespace, relation.name)):
        raise ValueError("Redshift namespace and relation identifiers cannot exceed 127 bytes")


__all__ = ["RedshiftWarehouseConfig", "validate_redshift_relation"]
