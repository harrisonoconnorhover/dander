# Morning Handoff

## Finished

- Merged the Azure launcher/Key Vault contract through protected PR #188 with green main CI.
- Added plan-first Azure stage zero for firewall-restricted Storage state, Basic ACR, and one user-assigned runtime identity.
- Added Container Apps Jobs Terraform for exact projections, Key Vault references, logs, alerts, and optional internal networking.
- Added separate plan/apply CLI paths and a read-only, secret-free Azure deployment verifier.
- Kept provider registration, candidate publication, resource creation, image copy, and job execution behind later approvals.

## Try It

Run `terraform -chdir=infra/azure test` and `uv run pytest -q
tests/bootstrap/test_azure_admin.py tests/bootstrap/test_azure_terraform.py
tests/providers/test_azure_deployment_verification.py`.

## Checks

- Full `pytest -q`, Ruff, strict mypy, Terraform validate/provider-mocked tests, wheel/sdist inspection, and Trivy HIGH/CRITICAL config scan pass.
- Protected main CI is green at merged PR #188; protected CI for this infrastructure slice is pending.
- Azure CLI is signed in; required resource providers remain unregistered and no Azure resource exists from this work.

## Decisions

- Terraform disables automatic provider registration and uses saved-plan-only applies with Entra-authenticated state.
- State and Key Vault networks default to deny and admit one reviewed exact operator IP; Key Vault also keeps Azure's trusted-service path.
- Paused projections are manual jobs; active schedules retain the already validated UTC cron.

## Remaining

- Merge this infrastructure/verifier slice through a focused protected PR.
- Add digest-preserving ACR copy and launcher lifecycle operations without provider writes.
- Prepare Azure-to-Google federation and named Snowflake proof tooling locally.
- Obtain publication and provider-cost approvals before candidate or live proof mutations.
- Run approved identity, lifecycle, cleanup, rollback, and no-drift acceptance.

## Review First

- `infra/azure/modules/container-apps-jobs/main.tf`
- `src/dander/bootstrap/azure_admin.py`
- `src/dander/providers/azure_container_apps/verification.py`
