"""Dependency-light Snowflake warehouse configuration."""

from dander.providers.snowflake.config import (
    SnowflakeKeyPairAuth,
    SnowflakeOAuthAuth,
    SnowflakeWarehouseConfig,
)
from dander.providers.snowflake.preflight import (
    SnowflakeStagingAuthority,
    SnowflakeStagingAuthorityError,
    verify_snowflake_staging_authority,
)

__all__ = [
    "SnowflakeKeyPairAuth",
    "SnowflakeOAuthAuth",
    "SnowflakeStagingAuthority",
    "SnowflakeStagingAuthorityError",
    "SnowflakeWarehouseConfig",
    "verify_snowflake_staging_authority",
]
