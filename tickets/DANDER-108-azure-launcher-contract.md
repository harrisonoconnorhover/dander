# DANDER-108 — Azure launcher and Key Vault contract

Status: implemented locally; live qualification pending

## Requirement

Add Azure Container Apps Jobs and Azure Key Vault to the existing typed provider boundaries before
Terraform or live-provider work. The contract must consume the immutable provider-neutral
`ResolvedTemplateRequest`, preserve the OCI runtime command, require an ACR digest, keep secret
values out of projections, and reject Azure semantics that cannot be represented exactly.

## Acceptance

- [x] `azure_container_apps` is a lazy launcher provider with explicit resource and extension limits.
- [x] The launcher projects an immutable ACR digest, user-assigned managed identity, Container Apps
  environment, bounded retries/deadline, logs, and Key Vault references.
- [x] Scheduled jobs accept only five-field UTC cron because Azure evaluates job schedules in UTC.
- [x] Unsupported CPU/memory pairs, foreign registries, malformed secret ids, and incompatible
  launcher/secret-provider combinations fail before provider access.
- [x] `azure_key_vault` resolves only full vault secret URIs, remains SDK-lazy, audits references,
  excludes values, and cannot cross vault boundaries through one resolver.
- [x] Version 2 configuration resolves the named Azure/Snowflake/PostgreSQL/no-catalog shape.
- [ ] Terraform, ACR promotion, Azure operations, federation, and live profile proofs remain separate
  Phase 6 tickets and require explicit approval before provider writes.

## Evidence boundary

This ticket is deterministic local contract evidence only. It creates no Azure resources, registers
no resource providers, copies no image, and makes no support claim.
