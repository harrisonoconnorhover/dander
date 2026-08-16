"""Read-only Snowflake staging-authority preflight coverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

import pytest

from dander.providers.snowflake import SnowflakeWarehouseConfig
from dander.providers.snowflake.preflight import (
    SnowflakeStagingAuthorityError,
    verify_snowflake_staging_authority,
)


@dataclass
class _Snowflake:
    grants: tuple[object, ...]
    statements: list[str] = field(default_factory=list)
    fail_grants: bool = False
    closes: int = 0

    def connect(self) -> _Connection:
        return _Connection(self)


class _Connection:
    def __init__(self, backend: _Snowflake) -> None:
        self.backend = backend

    def cursor(self) -> _Cursor:
        return _Cursor(self.backend)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.backend.closes += 1


class _Cursor:
    def __init__(self, backend: _Snowflake) -> None:
        self.backend = backend
        self.rowcount = 0
        self.sfqid: str | None = None
        self._row: object | None = None
        self._rows: list[object] = []

    def execute(self, command: str, params: object = None) -> Self:
        assert params is None
        compact = " ".join(command.split())
        self.backend.statements.append(compact)
        self.sfqid = f"query-{len(self.backend.statements)}"
        if compact == "SELECT CURRENT_DATABASE(), CURRENT_ROLE(), CURRENT_WAREHOUSE()":
            self._row = ("DANDER_TEST", "DANDER_ROLE", "DANDER_WH")
        elif compact == 'SHOW GRANTS TO ROLE "DANDER_ROLE" LIMIT 1000':
            if self.backend.fail_grants:
                raise RuntimeError("private provider response with token-value and row-value")
            self._rows = list(self.backend.grants)
            self.rowcount = len(self._rows)
        return self

    def executemany(self, command: str, seq_of_parameters: object) -> Self:
        raise AssertionError((command, seq_of_parameters))

    def fetchone(self) -> object | None:
        return self._row

    def fetchall(self) -> list[object]:
        return list(self._rows)

    def close(self) -> None:
        return None


def _config(*, role: str | None = "DANDER_ROLE") -> SnowflakeWarehouseConfig:
    return SnowflakeWarehouseConfig.model_validate(
        {
            "provider": "snowflake",
            "account": "org-account",
            "user": "DANDER_USER",
            "database": "DANDER_TEST",
            "schema": "RAW",
            "warehouse": "DANDER_WH",
            "role": role,
            "auth": {
                "method": "oauth",
                "token_env": "DANDER_SNOWFLAKE_OAUTH_TOKEN",
            },
        }
    )


def _grant(privilege: str) -> tuple[object, ...]:
    return (
        "2026-08-16T00:00:00Z",
        privilege,
        "DATABASE",
        "DANDER_TEST",
        "ROLE",
        "DANDER_ROLE",
        "false",
        "ACCOUNTADMIN",
    )


def test_staging_authority_preflight_reads_only_the_selected_role_grants() -> None:
    backend = _Snowflake(grants=(_grant("USAGE"), _grant("CREATE SCHEMA")))

    authority = verify_snowflake_staging_authority(
        _config(),
        connection_factory=backend.connect,
    )

    assert authority.as_dict() == {
        "database": "DANDER_TEST",
        "role": "DANDER_ROLE",
        "warehouse": "DANDER_WH",
        "create_schema": True,
        "staging_schema_lifecycle": True,
    }
    assert backend.statements == [
        "SELECT CURRENT_DATABASE(), CURRENT_ROLE(), CURRENT_WAREHOUSE()",
        'SHOW GRANTS TO ROLE "DANDER_ROLE" LIMIT 1000',
    ]
    assert backend.closes == 1


def test_staging_authority_preflight_names_the_exact_missing_privilege() -> None:
    backend = _Snowflake(grants=(_grant("USAGE"),))

    with pytest.raises(
        SnowflakeStagingAuthorityError,
        match=("requires CREATE SCHEMA on database 'DANDER_TEST' for role 'DANDER_ROLE'"),
    ):
        verify_snowflake_staging_authority(
            _config(),
            connection_factory=backend.connect,
        )

    assert backend.closes == 1


def test_staging_authority_preflight_sanitizes_provider_failures() -> None:
    backend = _Snowflake(grants=(), fail_grants=True)

    with pytest.raises(SnowflakeStagingAuthorityError) as captured:
        verify_snowflake_staging_authority(
            _config(),
            connection_factory=backend.connect,
        )

    assert "could not inspect the configured role grants" in str(captured.value)
    assert "token-value" not in str(captured.value)
    assert "row-value" not in str(captured.value)
    assert backend.closes == 1


def test_staging_authority_preflight_requires_an_explicit_role_before_connecting() -> None:
    backend = _Snowflake(grants=())

    with pytest.raises(
        SnowflakeStagingAuthorityError,
        match="requires an explicit configured role",
    ):
        verify_snowflake_staging_authority(
            _config(role=None),
            connection_factory=backend.connect,
        )

    assert backend.statements == []
    assert backend.closes == 0
