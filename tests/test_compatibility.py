"""The installed backend matrix is explicit, deterministic, and fail-closed."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dander.cli.main import app
from dander.compatibility import (
    CompatibilityError,
    CompatibilityStatus,
    load_runtime_compatibility,
)
from dander.providers.bigquery.runtime import BIGQUERY_CAPABILITIES
from dander.providers.postgresql.runtime import POSTGRESQL_CAPABILITIES
from dander.providers.redshift.runtime import REDSHIFT_CAPABILITIES
from dander.providers.snowflake.runtime import SNOWFLAKE_CAPABILITIES


def test_state_warehouse_matrix_covers_every_current_pair() -> None:
    matrix = load_runtime_compatibility()

    assert matrix.schema == "io.dander.runtime.compatibility/v1"
    assert {(pair.state, pair.warehouse): pair.status for pair in matrix.state_warehouse_pairs} == {
        ("bigquery", "bigquery"): CompatibilityStatus.SUPPORTED,
        ("bigquery", "postgresql"): CompatibilityStatus.EXPERIMENTAL,
        ("bigquery", "snowflake"): CompatibilityStatus.EXPERIMENTAL,
        ("bigquery", "redshift"): CompatibilityStatus.EXPERIMENTAL,
        ("postgresql", "bigquery"): CompatibilityStatus.UNSUPPORTED,
        ("postgresql", "postgresql"): CompatibilityStatus.EXPERIMENTAL,
        ("postgresql", "snowflake"): CompatibilityStatus.EXPERIMENTAL,
        ("postgresql", "redshift"): CompatibilityStatus.EXPERIMENTAL,
    }


@pytest.mark.parametrize(
    ("state", "warehouse"),
    [
        ("bigquery", "bigquery"),
        ("bigquery", "postgresql"),
        ("bigquery", "snowflake"),
        ("bigquery", "redshift"),
        ("postgresql", "postgresql"),
        ("postgresql", "snowflake"),
        ("postgresql", "redshift"),
    ],
)
def test_executable_pairs_are_admitted(state: str, warehouse: str) -> None:
    pair = load_runtime_compatibility().require_executable(state=state, warehouse=warehouse)

    assert pair.status is not CompatibilityStatus.UNSUPPORTED


def test_unsupported_and_unlisted_pairs_fail_with_a_reason() -> None:
    matrix = load_runtime_compatibility()

    with pytest.raises(CompatibilityError, match="every BigQuery write mode"):
        matrix.require_executable(state="postgresql", warehouse="bigquery")
    with pytest.raises(CompatibilityError, match="not in the installed compatibility matrix"):
        matrix.require_executable(state="sqlite", warehouse="postgresql")


def test_runtime_compatibility_cli_prints_one_deterministic_document() -> None:
    result = CliRunner().invoke(app, ["runtime", "compatibility"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "io.dander.runtime.compatibility/v1"
    assert len(payload["state_warehouse_pairs"]) == 8
    assert [warehouse["provider"] for warehouse in payload["warehouses"]] == [
        "bigquery",
        "postgresql",
        "redshift",
        "snowflake",
    ]
    assert result.output.strip() == load_runtime_compatibility().to_json()


def test_published_warehouse_capabilities_match_runtime_declarations() -> None:
    runtime_capabilities = {
        capability.provider_id: capability
        for capability in (
            BIGQUERY_CAPABILITIES,
            POSTGRESQL_CAPABILITIES,
            REDSHIFT_CAPABILITIES,
            SNOWFLAKE_CAPABILITIES,
        )
    }

    for report in load_runtime_compatibility().warehouses:
        capability = runtime_capabilities[report.provider]
        assert report.write_modes == tuple(sorted(mode.value for mode in capability.write_modes))
        assert report.transports == tuple(
            sorted(transport.value for transport in capability.transports)
        )
        assert report.logical_types == tuple(
            sorted(kind.value for kind in capability.schema_support.logical_types)
        )
        assert report.max_decimal_precision == capability.schema_support.max_decimal_precision
        assert report.max_temporal_precision == capability.schema_support.max_temporal_precision
        assert report.supports_transforms is capability.supports_transforms
        assert report.supports_graphs is capability.supports_graphs
        assert report.supports_target_fencing is capability.supports_target_fencing
