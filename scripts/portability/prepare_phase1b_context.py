"""Add only the Phase 1B probe and non-secret external-account config to a generated project."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


class ContextPreparationError(RuntimeError):
    """Raised when the source-free proof context is invalid."""


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _credential_config(path: Path, *, token_lifetime_seconds: int) -> dict[str, object]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPreparationError("Credential configuration is not valid JSON") from error
    if not isinstance(config, dict) or config.get("type") != "external_account":
        raise ContextPreparationError("Phase 1B requires an external-account credential config")
    source = config.get("credential_source")
    if not isinstance(source, dict) or source.get("environment_id") != "aws1":
        raise ContextPreparationError("Credential configuration is not for an AWS workload")
    if "private_key" in config or "client_secret" in config:
        raise ContextPreparationError("Credential configuration contains a prohibited secret")
    impersonation_url = config.get("service_account_impersonation_url")
    if not isinstance(impersonation_url, str) or not impersonation_url.startswith(
        "https://iamcredentials.googleapis.com/"
    ):
        raise ContextPreparationError("Credential configuration must impersonate a service account")
    config["service_account_impersonation_options"] = {
        "token_lifetime_seconds": token_lifetime_seconds
    }
    return config


def prepare_context(
    *,
    project_dir: Path,
    credential_config: Path,
    probe_script: Path,
    token_lifetime_seconds: int = 600,
) -> None:
    """Prepare an ephemeral generated project for the Phase 1B image build."""
    if (project_dir / "src").exists():
        raise ContextPreparationError("Phase 1B requires a generated source-free project")
    dockerfile = project_dir / "Dockerfile"
    dockerignore = project_dir / ".dockerignore"
    required = (dockerfile, dockerignore, project_dir / "dander.yaml", probe_script)
    if any(not path.is_file() for path in required):
        raise ContextPreparationError("Generated project or proof tooling is incomplete")
    if not 300 <= token_lifetime_seconds <= 900:
        raise ContextPreparationError("Proof token lifetime must be between 300 and 900 seconds")

    config = _credential_config(
        credential_config,
        token_lifetime_seconds=token_lifetime_seconds,
    )
    shutil.copyfile(probe_script, project_dir / "phase1b_probe.py")
    _atomic_text(
        project_dir / "gcp-wif.json",
        json.dumps(config, indent=2, sort_keys=True) + "\n",
    )

    dockerfile_content = dockerfile.read_text(encoding="utf-8")
    marker = "\nUSER 65532:65532\n"
    if marker not in dockerfile_content:
        raise ContextPreparationError("Generated Dockerfile has no non-root user boundary")
    additions = (
        "\nCOPY --chown=65532:65532 phase1b_probe.py ./phase1b_probe.py\n"
        "COPY --chown=65532:65532 gcp-wif.json ./gcp-wif.json\n"
    )
    if "COPY --chown=65532:65532 phase1b_probe.py ./phase1b_probe.py" not in dockerfile_content:
        dockerfile_content = dockerfile_content.replace(marker, additions + marker, 1)
    _atomic_text(dockerfile, dockerfile_content)

    ignore_lines = dockerignore.read_text(encoding="utf-8").rstrip().splitlines()
    for line in ("!phase1b_probe.py", "!gcp-wif.json"):
        if line not in ignore_lines:
            ignore_lines.append(line)
    _atomic_text(
        dockerignore,
        "\n".join(ignore_lines) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--credential-config", required=True, type=Path)
    parser.add_argument(
        "--probe-script",
        type=Path,
        default=Path(__file__).with_name("wif_bigquery_probe.py"),
    )
    parser.add_argument("--token-lifetime-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        prepare_context(
            project_dir=args.project_dir.resolve(),
            credential_config=args.credential_config.resolve(),
            probe_script=args.probe_script.resolve(),
            token_lifetime_seconds=args.token_lifetime_seconds,
        )
    except ContextPreparationError as error:
        print(f"Phase 1B context preparation failed: {error}")
        return 1
    print(f"Prepared source-free Phase 1B context: {args.project_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
