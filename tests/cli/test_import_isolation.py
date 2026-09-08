"""Local CLI commands must work without loading provider or extraction SDKs."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "command", ("--version", "--help", "new", "validate", "control", "compatibility")
)
def test_local_commands_do_not_import_provider_sdks(command: str, tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import importlib.abc
import sys
from pathlib import Path

forbidden = (
    "google.auth", "google.api_core", "google.cloud", "boto3", "botocore",
    "azure", "oci", "snowflake", "psycopg",
    "dlt", "duckdb", "pandas", "pyarrow", "pyspark",
)
attempted = []

class BlockProviderSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in forbidden):
            attempted.append(fullname)
            raise ImportError("Local command attempted provider import: " + fullname)

sys.meta_path.insert(0, BlockProviderSDKs())
from typer.testing import CliRunner
from dander.cli.main import app

command = sys.argv[1]
project = Path("starter")
if command == "validate":
    from dander.project import scaffold_project
    scaffold_project(project)
    args = [command, "--config", str(project / "dander.yaml")]
elif command == "new":
    args = [command, str(project)]
elif command == "control":
    args = ["control", "--help"]
elif command == "compatibility":
    args = ["runtime", "compatibility"]
else:
    args = [command]

result = CliRunner().invoke(app, args)
assert result.exit_code == 0, (result.output, result.exception)
assert attempted == [], attempted
assert not any(
    name == prefix or name.startswith(prefix + ".")
    for name in sys.modules for prefix in forbidden
)
if command == "new":
    assert (project / "dander.yaml").is_file()
if command == "validate":
    assert "Validated" in result.output
print(result.output)
""",
            command,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("profile", ("local", "postgres"))
def test_local_and_postgres_modules_do_not_import_google_or_dlt(profile: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import importlib.abc
import sys

forbidden = ("google.auth", "google.api_core", "google.cloud", "dlt")

class BlockUnselectedSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in forbidden):
            raise ImportError("Unselected provider dependency: " + fullname)

sys.meta_path.insert(0, BlockUnselectedSDKs())
from dander.identity import AzureContainerAppsIdentityError, prepare_azure_google_identity
from dander.runtime import PipelineRunner
from dander.control.startup_factory import build_control_startup
try:
    prepare_azure_google_identity(environ={})
except AzureContainerAppsIdentityError:
    pass
else:
    raise AssertionError("Missing identity configuration must still fail closed")
if sys.argv[1] == "postgres":
    from dander.providers.postgresql.runtime import PostgreSQLSchemaMapper
    from dander.providers.postgresql.state import POSTGRESQL_STATE_FACTORY
    from dander.control.postgresql_run_store import PostgreSQLRunStore
    from dander.writer import WriteField
    schema = PostgreSQLSchemaMapper().canonical_schema((WriteField("id", "INT64"),))
    assert schema.fields[0].name == "id"
else:
    from dander.state import SqliteRunHistoryStore, SqliteWatermarkStore
assert not any(
    name == prefix or name.startswith(prefix + ".")
    for name in sys.modules for prefix in forbidden
)
""",
            profile,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
