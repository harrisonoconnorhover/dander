"""Dependency-light Snowflake warehouse configuration."""

from dander.providers.snowflake.config import (
    SnowflakeKeyPairAuth,
    SnowflakeOAuthAuth,
    SnowflakeWarehouseConfig,
)

__all__ = [
    "SnowflakeKeyPairAuth",
    "SnowflakeOAuthAuth",
    "SnowflakeWarehouseConfig",
]
