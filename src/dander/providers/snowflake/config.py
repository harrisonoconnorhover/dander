"""Validated Snowflake settings containing references, never credentials."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dander.warehouse.contracts import RelationRef

_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,254}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,254}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,126}$")


class SnowflakeOAuthAuth(BaseModel):
    """OAuth access-token reference projected into the runtime environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["oauth"]
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"

    @field_validator("token_env")
    @classmethod
    def validate_token_env(cls, value: str) -> str:
        return _environment_name(value)


class SnowflakeKeyPairAuth(BaseModel):
    """Key-pair file references projected into the runtime environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["key_pair"]
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None

    @field_validator("private_key_file_env", "private_key_password_env")
    @classmethod
    def validate_key_env(cls, value: str | None) -> str | None:
        return None if value is None else _environment_name(value)


SnowflakeAuth = Annotated[
    SnowflakeOAuthAuth | SnowflakeKeyPairAuth,
    Field(discriminator="method"),
]


class SnowflakeWarehouseConfig(BaseModel):
    """Snowflake connection, staging, and bounded execution settings."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    provider: Literal["snowflake"]
    account: str
    user: str
    database: str
    schema_name: str = Field(default="raw", alias="schema")
    warehouse: str
    role: str | None = None
    auth: SnowflakeAuth
    statement_timeout_seconds: int = Field(default=900, ge=1, le=86_400)
    queued_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    login_timeout_seconds: int = Field(default=30, ge=1, le=300)
    network_timeout_seconds: int = Field(default=300, ge=1, le=3_600)
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
        le=16 * 1_024 * 1_024,
    )

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        if not _ACCOUNT.fullmatch(value):
            raise ValueError("account must be a Snowflake account identifier")
        return value

    @field_validator("user", "database", "schema_name", "warehouse", "role")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("Snowflake identifiers must use portable identifier syntax")
        return value

    @model_validator(mode="after")
    def validate_direct_thresholds(self) -> SnowflakeWarehouseConfig:
        """Require both provisional direct-load limits or disable the path entirely."""
        if (self.direct_max_rows == 0) != (self.direct_max_logical_bytes == 0):
            raise ValueError(
                "direct_max_rows and direct_max_logical_bytes must both be zero or positive"
            )
        return self

    def raw_relation(
        self,
        name: str,
        *,
        compatibility_catalog: str | None,
        compatibility_namespace: str | None,
    ) -> RelationRef:
        """Translate compatibility inputs into Snowflake database/schema coordinates."""
        del compatibility_catalog
        return RelationRef(
            catalog=self.database,
            namespace=compatibility_namespace or self.schema_name,
            name=name,
        )


def _environment_name(value: str) -> str:
    if not _ENVIRONMENT_NAME.fullmatch(value):
        raise ValueError("credential references must be uppercase environment-variable names")
    return value


__all__ = [
    "SnowflakeAuth",
    "SnowflakeKeyPairAuth",
    "SnowflakeOAuthAuth",
    "SnowflakeWarehouseConfig",
]
