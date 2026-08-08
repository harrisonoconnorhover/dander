"""Dependency-light PostgreSQL provider configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,126}$")
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_AUTHORITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")


class PostgreSQLStateConfig(BaseModel):
    """PostgreSQL durable-state connection and bounded-pool settings.

    The manifest stores only the name of an environment variable. The connection
    string itself remains runtime-injected secret material.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["postgresql"]
    authority_id: str
    authority_epoch: int = Field(default=1, ge=1)
    dsn_env: str = "DANDER_POSTGRES_DSN"
    schema_name: str = "dander_meta"
    pool_min_size: int = Field(default=1, ge=1, le=20)
    pool_max_size: int = Field(default=5, ge=1, le=50)
    pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    lease_seconds: int = Field(default=120, ge=10, le=3600)
    terminal_history_retention_days: int = Field(default=90, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_settings(self) -> PostgreSQLStateConfig:
        if not _ENVIRONMENT_NAME.fullmatch(self.dsn_env):
            raise ValueError("dsn_env must be an uppercase environment-variable name")
        if not _SCHEMA_NAME.fullmatch(self.schema_name):
            raise ValueError("schema_name must be a safe lowercase PostgreSQL identifier")
        if not _AUTHORITY_ID.fullmatch(self.authority_id):
            raise ValueError("authority_id must be a stable non-secret deployment identifier")
        if self.pool_min_size > self.pool_max_size:
            raise ValueError("pool_min_size must not exceed pool_max_size")
        return self


__all__ = ["PostgreSQLStateConfig"]
