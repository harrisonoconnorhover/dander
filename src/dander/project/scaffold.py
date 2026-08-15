"""Create a complete Dander project from versioned package resources."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from dander import __version__

if TYPE_CHECKING:
    from collections.abc import Iterable

_SOURCE_ASSETS = (
    (Path("connectors/greenhouse_job_board.yaml"), Path("connectors/greenhouse_job_board.yaml")),
    (Path("connectors/phase8_aws_fixture.yaml"), Path("connectors/phase8_aws_fixture.yaml")),
    (Path("graphs/greenhouse_jobs.yaml"), Path("graphs/greenhouse_jobs.yaml")),
    (
        Path("models/staging/stg_greenhouse__jobs.sql"),
        Path("models/staging/stg_greenhouse__jobs.sql"),
    ),
    (
        Path("models/staging/stg_greenhouse__jobs.postgres.sql"),
        Path("models/staging/stg_greenhouse__jobs.postgres.sql"),
    ),
    (
        Path("models/staging/stg_greenhouse__jobs.yml"),
        Path("models/staging/stg_greenhouse__jobs.yml"),
    ),
    (
        Path("models/staging/stg_phase8_aws__posts.sql"),
        Path("models/staging/stg_phase8_aws__posts.sql"),
    ),
    (
        Path("models/staging/stg_phase8_aws__posts.yml"),
        Path("models/staging/stg_phase8_aws__posts.yml"),
    ),
    (Path("examples/salesforce/dander.yaml"), Path("examples/salesforce/dander.yaml")),
    (
        Path("connectors/salesforce_jwt.example.yaml"),
        Path("examples/salesforce/connectors/salesforce.yaml"),
    ),
    *(
        (
            Path(f"models/{folder}/{name}.{suffix}"),
            Path(f"examples/salesforce/models/{folder}/{name}.{suffix}"),
        )
        for folder, name in (
            ("staging", "stg_salesforce__users"),
            ("staging", "stg_salesforce__accounts"),
            ("staging", "stg_salesforce__contacts"),
            ("staging", "stg_salesforce__opportunities"),
            ("marts", "fct_salesforce__opportunities"),
        )
        for suffix in ("sql", "yml")
    ),
)


class ProjectScaffoldError(RuntimeError):
    """Raised when a starter project cannot be created without overwriting work."""


def scaffold_project(destination: Path) -> Path:
    """Atomically create a runnable starter project and return its absolute path."""
    requested = destination.expanduser()
    if requested.exists() or requested.is_symlink():
        raise ProjectScaffoldError(f"Destination already exists: {requested.absolute()}")
    target = requested.resolve()
    if target.exists() or target.is_symlink():
        raise ProjectScaffoldError(f"Destination already exists: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=f".{target.name}-", dir=target.parent) as temporary:
            staging = Path(temporary) / "project"
            template = resources.files("dander").joinpath("templates", "project")
            with resources.as_file(template) as template_path:
                shutil.copytree(template_path, staging)
            source_root = _source_checkout_root()
            for source_path, destination_path in _SOURCE_ASSETS:
                packaged = staging / destination_path
                if packaged.is_file():
                    continue
                if source_root is None or not (source_root / source_path).is_file():
                    raise ProjectScaffoldError(
                        f"Installed package is missing starter asset: {destination_path}"
                    )
                packaged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_root / source_path, packaged)
            _render_distribution_version(staging / "Dockerfile")
            if not (staging / "infra" / "main.tf").is_file():
                if source_root is None:
                    raise ProjectScaffoldError("Installed package is missing Terraform assets")
                shutil.copytree(
                    source_root / "infra",
                    staging / "infra",
                    ignore=_ignored_infra_files,
                )
            if target.exists() or target.is_symlink():
                raise ProjectScaffoldError(f"Destination already exists: {target}")
            staging.rename(target)
    except ProjectScaffoldError:
        raise
    except OSError as error:
        raise ProjectScaffoldError(f"Could not create Dander project at {target}") from error
    return target


def _render_distribution_version(dockerfile: Path) -> None:
    placeholder = "__DANDER_DISTRIBUTION_VERSION__"
    content = dockerfile.read_text(encoding="utf-8")
    if content.count(placeholder) != 1:
        raise ProjectScaffoldError("Starter Dockerfile has an invalid version placeholder")
    dockerfile.write_text(content.replace(placeholder, __version__), encoding="utf-8")


def _source_checkout_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[3]
    return candidate if (candidate / "infra" / "main.tf").is_file() else None


def _ignored_infra_files(_: str, names: Iterable[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {".DS_Store", ".terraform", "__pycache__", "sandbox.auto.tfvars"}
        or name.endswith(".tfplan")
        or ".tfstate" in name
        or (name.endswith(".tfvars") and not name.endswith(".tfvars.example"))
    }
