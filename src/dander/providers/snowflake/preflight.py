"""Read-only Snowflake authority checks for qualification setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.providers.registry import ProviderFactoryError
from dander.providers.snowflake.session import execute, open_connection

if TYPE_CHECKING:
    from dander.providers.snowflake.config import SnowflakeWarehouseConfig
    from dander.providers.snowflake.session import SnowflakeConnectionFactory

_SHOW_GRANTS_LIMIT = 1_000


class SnowflakeStagingAuthorityError(RuntimeError):
    """Raised when the selected role cannot own Dander staging schemas."""


@dataclass(frozen=True, slots=True)
class SnowflakeStagingAuthority:
    """Sanitized proof that one role can create and own disposable staging schemas."""

    database: str
    role: str
    warehouse: str
    create_schema: bool = True
    staging_schema_lifecycle: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        """Return credential-free preflight output."""
        return {
            "database": self.database,
            "role": self.role,
            "warehouse": self.warehouse,
            "create_schema": self.create_schema,
            "staging_schema_lifecycle": self.staging_schema_lifecycle,
        }


def verify_snowflake_staging_authority(
    config: SnowflakeWarehouseConfig,
    *,
    connection_factory: SnowflakeConnectionFactory | None = None,
) -> SnowflakeStagingAuthority:
    """Prove the active role has the database grant needed by owned staging schemas.

    The check reads the current session coordinates and role grants. It never creates a schema,
    starts a candidate, or includes raw provider rows or errors in its failure messages.
    """
    role = config.role
    if role is None:
        raise SnowflakeStagingAuthorityError(
            "Snowflake staging-schema preflight requires an explicit configured role"
        )
    if connection_factory is None:
        from dander.providers.snowflake.runtime import _sdk_connection_factory

        try:
            connection_factory = _sdk_connection_factory(config)
        except ProviderFactoryError as error:
            raise SnowflakeStagingAuthorityError(str(error)) from error

    try:
        with open_connection(connection_factory) as connection:
            current = execute(
                connection,
                "SELECT CURRENT_DATABASE(), CURRENT_ROLE(), CURRENT_WAREHOUSE()",
                fetch="one",
            ).row
            grants = execute(
                connection,
                f"SHOW GRANTS TO ROLE {_quote(role)} LIMIT {_SHOW_GRANTS_LIMIT}",
                fetch="all",
            ).rows
    except Exception as error:
        raise SnowflakeStagingAuthorityError(
            "Snowflake staging-schema preflight could not inspect the configured role grants"
        ) from error

    _require_session_coordinates(current, config)
    if not any(_is_create_schema_grant(row, database=config.database, role=role) for row in grants):
        raise SnowflakeStagingAuthorityError(
            f"Snowflake staging-schema preflight requires CREATE SCHEMA on database "
            f"{config.database!r} for role {role!r}"
        )
    return SnowflakeStagingAuthority(
        database=config.database,
        role=role,
        warehouse=config.warehouse,
    )


def _require_session_coordinates(current: object, config: SnowflakeWarehouseConfig) -> None:
    if not isinstance(current, (tuple, list)) or len(current) < 3:
        raise SnowflakeStagingAuthorityError(
            "Snowflake staging-schema preflight returned invalid session coordinates"
        )
    expected = (config.database, config.role, config.warehouse)
    labels = ("database", "role", "warehouse")
    for label, actual, selected in zip(labels, current, expected, strict=True):
        if selected is None or str(actual).casefold() != selected.casefold():
            raise SnowflakeStagingAuthorityError(
                f"Snowflake staging-schema preflight selected the wrong {label}"
            )


def _is_create_schema_grant(row: object, *, database: str, role: str) -> bool:
    if not isinstance(row, (tuple, list)) or len(row) < 6:
        return False
    privilege, granted_on, object_name, grantee = row[1], row[2], row[3], row[5]
    expected = ("create schema", "database", database, role)
    return all(
        str(actual).casefold() == selected.casefold()
        for actual, selected in zip(
            (privilege, granted_on, object_name, grantee),
            expected,
            strict=True,
        )
    )


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


__all__ = [
    "SnowflakeStagingAuthority",
    "SnowflakeStagingAuthorityError",
    "verify_snowflake_staging_authority",
]
