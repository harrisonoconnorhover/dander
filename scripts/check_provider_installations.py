#!/usr/bin/env python3
"""Smoke-test built distributions in clean environments outside the checkout.

The caller owns work-directory cleanup; generated projects remain available for
subsequent Terraform validation.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GCP_IMPORTS = (
    "dlt",
    "google.auth",
    "google.cloud.bigquery",
    "google.cloud.bigquery_storage",
    "google.cloud.dataplex_v1",
    "google.cloud.secretmanager",
    "google.cloud.storage",
)
POSTGRES_IMPORTS = (
    "psycopg",
    "psycopg_pool",
    "dander.providers.postgresql.runtime",
    "dander.providers.postgresql.state",
    "dander.control.postgresql_graph_store",
    "dander.control.postgresql_run_store",
)
GOOGLE_DISTRIBUTIONS = (
    "google-api-core",
    "google-auth",
    "google-cloud-core",
    "google-cloud-bigquery",
    "google-cloud-bigquery-storage",
    "google-cloud-dataplex",
    "google-cloud-secret-manager",
    "google-cloud-storage",
    "google-resumable-media",
    "googleapis-common-protos",
)
PROFILE_IMPORTS = {
    "base-wheel": (),
    "base-sdist": (),
    "postgres": POSTGRES_IMPORTS,
    "bigquery-gcp": GCP_IMPORTS,
    "runtime-all": (
        *GCP_IMPORTS,
        *POSTGRES_IMPORTS,
        "azure.identity",
        "azure.keyvault.secrets",
        "azure.storage.blob",
        "boto3",
        "oci",
        "pyarrow",
        "redshift_connector",
        "snowflake.connector",
    ),
}
PROFILE_EXTRAS = {
    "postgres": "postgres",
    "bigquery-gcp": "bigquery,gcp",
    "runtime-all": "runtime-all",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--python", default="3.12")
    parser.add_argument("--profile", choices=tuple(PROFILE_IMPORTS), action="append")
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    if work_dir.is_relative_to(ROOT):
        parser.error("--work-dir must be outside the source checkout")
    work_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    for profile in args.profile or PROFILE_IMPORTS:
        artifact = (args.sdist if profile == "base-sdist" else args.wheel).resolve()
        _check_profile(
            profile,
            artifact,
            work_dir=work_dir,
            version=args.version,
            python_version=args.python,
            environment=environment,
        )


def _check_profile(
    profile: str,
    artifact: Path,
    *,
    work_dir: Path,
    version: str,
    python_version: str,
    environment: dict[str, str],
) -> None:
    def run(*command: str, cwd: Path = work_dir) -> None:
        print(f"[{profile}] {shlex.join(command)}", flush=True)
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    venv = work_dir / f"venv-{profile}"
    project = work_dir / f"project-{profile}"
    if venv.exists() or project.exists():
        raise FileExistsError(f"Profile {profile} needs a fresh work directory")
    run("uv", "venv", "--python", python_version, str(venv))
    python = str(venv / "bin/python")
    console = str(venv / "bin/dander")
    requirement = str(artifact)
    if extra := PROFILE_EXTRAS.get(profile):
        requirement += f"[{extra}]"
    run("uv", "pip", "install", "--python", python, requirement)
    run(
        python,
        "-I",
        "-c",
        "import sys; from pathlib import Path; from importlib.metadata import version; "
        "import dander; assert Path(dander.__file__).is_relative_to(Path(sys.prefix)); "
        "assert version('dander-platform') == sys.argv[1]",
        version,
    )
    result = subprocess.run(
        (console, "--version"), cwd=work_dir, env=environment, text=True, capture_output=True
    )
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    result.check_returncode()
    if result.stdout.strip() != f"dander {version}":
        raise ValueError(f"Unexpected console version: {result.stdout!r}")
    for command in (("--help",), ("control", "--help"), ("runtime", "compatibility")):
        run(console, *command)
    run(console, "new", str(project))
    run(console, "validate", cwd=project)
    if f"ARG DANDER_VERSION={version}" not in (project / "Dockerfile").read_text():
        raise ValueError(f"Starter Dockerfile has the wrong version for {profile}")
    if modules := PROFILE_IMPORTS[profile]:
        run(
            python,
            "-I",
            "-c",
            "import importlib, sys; [importlib.import_module(name) for name in sys.argv[1:]]",
            *modules,
        )
    if profile == "runtime-all":
        run(
            python,
            "-I",
            "-c",
            "from dander.providers.dependencies import require_full_runtime; "
            "require_full_runtime()",
        )
    elif profile in {"base-wheel", "base-sdist", "postgres"}:
        run(
            python,
            "-I",
            "-c",
            "import sys; from importlib.metadata import distributions; "
            "installed = {dist.metadata['Name'].lower() for dist in distributions()}; "
            "unexpected = sorted(installed.intersection(sys.argv[1:])); "
            "assert not unexpected, f'Unexpected Google dependencies: {unexpected}'",
            *GOOGLE_DISTRIBUTIONS,
        )
    print(f"Validated {profile} installation and starter project: {project}", flush=True)


if __name__ == "__main__":
    main()
