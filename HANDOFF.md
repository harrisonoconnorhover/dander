# Morning Handoff

## Finished

- Basic CLI commands and local/PostgreSQL imports no longer load Google SDKs.
- Azure-to-Google credentials load only when selected; generic OAuth JWT signing uses the existing PyJWT dependency.
- Base and PostgreSQL installs omit Google SDKs; BigQuery/GCP and full-runtime extras preserve provider availability.
- Distribution CI exercises five fresh installation profiles outside the checkout, and selected BigQuery commands explain missing extras.

## Try It

Run `uv sync --frozen --extra dev --extra postgres --extra bigquery --extra gcp`, then `source .venv/bin/activate`.
Try `dander --help`, `dander control --help`, and `dander runtime compatibility`.
To verify built packages, run `python3 scripts/check_provider_installations.py dist/*.whl dist/*.tar.gz --work-dir /tmp/dander-install-check --version 0.9.0rc32` with a fresh work directory.

## Checks

- All 2,407 tests passed with disposable PostgreSQL 15; its container was removed afterward.
- Canonical strict typing passed for 507 files; Ruff lint/format, Control contract drift, and diff checks passed.
- Wheel/source archive validation passed. Base wheel, base source, PostgreSQL, BigQuery/GCP, and full-runtime fresh installations passed CLI, scaffold, and selected-SDK checks.
- JWT signature, claims, expiry, invalid-key behavior, and missing-BigQuery CLI errors passed offline.
- Fresh base installation: 97 to 60 distributions; installed-file metadata totals 290.1 to 52.3 MiB on this Mac. This is not a deployment memory/cost measurement.

## Decisions

- Keep provider SDK imports within selected provider operations.
- Full-runtime behavior remains available through its existing extra; package versions in the lockfile did not change.
- This remains unreleased source work; public RC20 and historical immutable images are unchanged.

## Remaining

- Integrate the separately tested PostgreSQL pending-recovery query and migration.
- Finish protected CI and exact-main verification for each runtime slice. No live provider qualification is part of this work.

## Review First

- `pyproject.toml`, `uv.lock`, and README installation profiles
- `scripts/check_provider_installations.py` and `.github/workflows/ci.yml`
- `src/dander/cli/run_command.py` and `src/dander/transform/runner.py`
