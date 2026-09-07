"""Local CLI commands must work without loading provider or extraction SDKs."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("command", ("--version", "--help", "new", "validate"))
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
    "google.cloud", "boto3", "botocore", "azure", "oci", "snowflake", "psycopg",
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
