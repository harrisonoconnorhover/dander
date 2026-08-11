# Morning Handoff

## Finished

- Kept protected main green through merged Azure contract, infrastructure, and lifecycle PRs #188-#190.
- Added renewable Container Apps managed-identity to Google credentials without files or static keys.
- Added the exact Azure BigQuery/Dataplex/GCP-secrets projection beside unchanged Snowflake/Key Vault behavior.
- Added disposable Entra/Google federation Terraform pinned to one Azure identity object ID.
- Added a bounded 600-second refresh probe with explicit confirmation and no automatic paid rerun.

## Try It

Run `uv run pytest -q tests/identity/test_azure_google.py tests/identity/test_refresh_probe.py
tests/providers/test_azure_container_apps_runtime.py`.

## Checks

- Full repository pytest, Ruff, and mypy pass with the federation, projection, CLI, and refresh code.
- Azure module and disposable federation Terraform mock tests pass; the new root validates cleanly.
- Protected main CI is green through merged PR #190; this federation slice is not yet published.
- No provider registration, resource creation, image copy, job execution, or paid query ran.

## Decisions

- Container Apps uses `ManagedIdentityCredential`; Google Auth exchanges tokens in memory for 600-second credentials.
- Google trust checks Entra tenant, application audience, and the exact managed-identity object ID.
- Azure Snowflake/Key Vault and Azure BigQuery/GCP-secrets remain distinct named profiles.

## Remaining

- Merge this federation slice through a focused protected PR.
- Prepare the named Azure Snowflake/PostgreSQL/Key-Vault acceptance tooling locally.
- Obtain candidate-publication approval before publishing or copying an image.
- Obtain explicit Azure, GCP, and Snowflake mutation ceilings before live proof.
- Run approved lifecycle, cleanup, rollback, and no-drift acceptance.

## Review First

- `src/dander/identity/azure_google.py`
- `src/dander/providers/azure_container_apps/runtime.py`
- `acceptance/cloud-portability/phase6/azure-google-federation/`
