# DANDER-109 — Plan and verify Azure Container Apps deployments

Status: merged through protected PR #189; live proof pending

## Requirement

Add a plan-first Azure Terraform path that creates the Phase 6 state, registry, identity, Container
Apps Jobs, Key Vault, logging, alerts, and networking contract without performing provider writes.
Add a read-only verifier that proves a deployed job still matches its manifest and immutable image.

## Acceptance

- [x] Stage zero creates a default-deny, operator-`/32`, private versioned Entra-authenticated state
  backend, ACR with static administrator credentials disabled, and one user-assigned runtime
  identity.
- [x] Automatic Azure resource-provider registration is disabled; registration remains a separately
  approved live mutation.
- [x] Initial stage-zero state migrates from a secured operator directory into Azure Storage only
  after applying the exact reviewed plan.
- [x] The platform root consumes exact Azure execution projections and existing stage-zero outputs.
- [x] Container Apps Jobs preserve digest, managed identity, deadline, retries, resources, UTC
  schedule/manual pause semantics, non-secret environment, and versionless Key Vault references.
- [x] Log Analytics, default-deny Key Vault networking with one exact operator IP, optional internal
  subnet placement, and failed-execution Action Group routing are explicit.
- [x] The CLI saves and applies plans as separate confirmed operations.
- [x] Read-only verification checks the exact subscription, environment, logs, job, immutable image,
  registry identity, ACR static-credential setting, and RBAC Key Vault without reading secrets.
- [x] Terraform provider-mocked tests and focused Python contract tests pass.
- [x] Protected CI passes and this ticket merges before any live Azure apply.

## Evidence boundary

This ticket contains local deterministic and provider-mocked evidence only. It does not register an
Azure provider, create a resource, copy an image, publish a candidate, read a secret, or spend cloud
credits. Live plan review, apply, cleanup, and no-drift evidence remain separate Phase 6 gates.
