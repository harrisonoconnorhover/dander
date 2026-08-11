# Morning Handoff

## Finished

- Confirmed protected main is green through merged PR #192.
- Corrected the false assumption that Container Apps can bypass a Key Vault firewall.
- Required Azure Key Vault profiles to supply one exact Container Apps infrastructure subnet.
- Kept the vault default-deny and admitted only that subnet plus the reviewed operator IP.
- Added fail-closed Terraform, verifier, and focused test coverage without contacting Azure.

## Try It

Run `uv run pytest tests/bootstrap/test_azure_terraform.py tests/providers/test_azure_deployment_verification.py` and `terraform -chdir=infra/azure test -no-color`.

## Checks

- Protected main CI is green through merged PR #192.
- Repository-wide Ruff, strict typing, and Python tests pass; Terraform format/validate and both Terraform test roots pass.
- No provider registration, resource creation, secret write, image copy, or paid operation ran.

## Decisions

- Managed identity grants permission but does not bypass Key Vault network controls.
- Key Vault profiles require an existing delegated subnet with the `Microsoft.KeyVault` service endpoint.
- The GCP-secret federation profile may still use Azure's managed network because it does not read this vault.

## Remaining

- Run local checks and merge this focused correction through protected CI.
- Prepare the named Azure Snowflake/PostgreSQL/Key-Vault acceptance preflight separately.
- Obtain candidate-publication approval before publishing or copying an image.
- Obtain explicit Azure, GCP, and Snowflake ceilings before live mutations.
- Run approved lifecycle, cleanup, rollback, and no-drift acceptance.

## Review First

- `infra/azure/modules/container-apps-jobs/main.tf`
- `src/dander/bootstrap/azure_terraform.py`
- `src/dander/providers/azure_container_apps/verification.py`
