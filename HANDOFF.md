# Morning Handoff

## Finished

- Basic CLI commands and local/PostgreSQL imports no longer load Google SDKs.
- Azure-to-Google credentials load only when selected; generic OAuth JWT signing uses the existing PyJWT dependency.
- Distribution CI now exercises five fresh installation profiles outside the checkout.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`, then `source .venv/bin/activate`.
Try `dander --help`, `dander control --help`, and `dander runtime compatibility`.
To verify built packages, run `python3 scripts/check_provider_installations.py dist/*.whl dist/*.tar.gz --work-dir /tmp/dander-install-check --version 0.9.0rc32` with a fresh work directory.

## Checks

- All 2,402 tests passed with disposable PostgreSQL 15; its container was removed afterward.
- Canonical strict typing passed for 507 files; Ruff lint/format, Control contract drift, and diff checks passed.
- Wheel/source archive validation passed. Base wheel, base source, PostgreSQL, BigQuery/GCP, and full-runtime fresh installations passed CLI, scaffold, and selected-SDK checks.
- JWT signature, claims, expiry, and invalid-key behavior passed without Google imports.

## Decisions

- Keep provider SDK imports within selected provider operations.
- Verify clean installations before changing dependency declarations; full-runtime behavior remains available through its existing extra.

## Remaining

- Move Google/BigQuery dependencies from the base installation into existing extras and document installation choices.
- Integrate the separately tested PostgreSQL pending-recovery query and migration.
- Finish protected CI and exact-main verification for each runtime slice. No live provider qualification is part of this work.

## Review First

- `src/dander/security/oauth_jwt.py` and `tests/security/test_enterprise_auth.py`
- `src/dander/identity/_azure_google_credentials.py` and `tests/cli/test_import_isolation.py`
- `scripts/check_provider_installations.py` and `.github/workflows/ci.yml`
