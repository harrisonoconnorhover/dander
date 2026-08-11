# Morning Handoff

## Finished

- Re-verified the Phase 5 exit gate on protected `main` and began Phase 6 locally.
- Added typed Azure Container Apps Jobs and Azure Key Vault provider contracts.
- Added deterministic ACR digest, managed identity, UTC schedule, resource, and secret projections.
- Kept all Azure provider writes behind a separate explicit cost/approval gate.

## Try It

Run `uv run --extra dev pytest -q tests/providers/test_azure_container_apps_runtime.py
tests/providers/test_azure_key_vault_runtime.py tests/project/test_portable_config.py`.

## Checks

- Focused Azure, launcher, secret-provider, dependency, and portable-config tests pass.
- Ruff passes on the Phase 6 contract scope.
- Azure subscription preflight is enabled; required resource providers remain unregistered.

## Decisions

- Azure cron is UTC-only and fails closed for other time zones.
- The first canonical Azure shape is Snowflake + PostgreSQL state + no catalog + Key Vault.
- Live proof must use a newly approved candidate built after the Azure implementation merges.

## Remaining

- Merge this contract through a focused protected PR.
- Add plan-first Azure state/ACR/Container Apps Terraform plus deployment verification.
- Add ACR copy and launcher operations without performing provider writes.
- Obtain publication and provider-cost approvals before candidate or live proof mutations.
- Run approved identity, lifecycle, cleanup, rollback, and no-drift acceptance.

## Review First

- `src/dander/providers/azure_container_apps/runtime.py`
- `src/dander/security/azure_key_vault.py`
- `tests/providers/test_azure_container_apps_runtime.py`
