# Morning Handoff

## Finished

- Routed the identity-refresh probe's explicit GCP project into its Azure deployment binding.
- Added `azure verify --gcp-project` for version-2 Azure/BigQuery deployments.
- Preserved reusable provider profiles without a fixed concrete GCP project.
- Added focused binding and CLI contract coverage.

## Try It

Run `uv run pytest tests/cli/test_azure_operations_cli.py tests/providers/test_azure_deployment_verification.py`.

## Checks

- Focused Azure CLI and deployment-verification tests passed (23 tests).
- Ruff check/format and mypy passed for the changed Python files.
- Protected CI remains to run.

## Decisions

- Reuse the probe's already-required project input at binding time.
- Keep the concrete GCP project out of reusable version-2 platform profiles.
- Preserve the accepted runtime digest; this is operator binding and verification behavior only.

## Remaining

- Merge this focused correction through protected CI.
- Resume the approved Azure-to-Google live federation plan and refresh proof.
- Clean up disposable Azure, Snowflake, and federation resources.
- Merge sanitized Phase 6 evidence and verify retained-GCP no drift.

## Review First

- `src/dander/cli/azure_command.py`
- `src/dander/providers/azure_container_apps/verification.py`
- `tests/cli/test_azure_operations_cli.py`
