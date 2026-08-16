---
id: DANDER-211
title: Bind the RC28 Azure canonical correctness objective
status: done
component: infrastructure
epic: cloud-portability-phase-8
depends_on: [DANDER-200, DANDER-209, DANDER-210]
created: 2026-08-16
---

## Context

Private RC28 includes the stable qualification entrypoint and the corrected Azure runtime-platform
handoff. The accepted Phase 6 lifecycle does not transfer to this candidate, and the USD 2 Azure
allocation cannot be consumed before one exact protected objective binds the candidate, provider
coordinates, disposable data plane, execution count, and cleanup boundary.

## Acceptance Criteria

- [x] One objective binds RC28, its immutable GAR/ACR digest, and the named Azure canonical profile.
- [x] One manual run and one success-conditional replay use the packaged synthetic scalar fixture.
- [x] Disposable Azure, PostgreSQL, and Snowflake coordinates, secret references, retry limits,
  maximum lifetime, exact cleanup, and the USD 2 ceiling are fixed before mutation.
- [x] Private operator coordinates must match committed hashes in a read-only preflight, while
  secret values remain outside Git.
- [x] Protected review and exact-main CI pass before any Azure, Snowflake, or PostgreSQL mutation.

## Design

Reuse the accepted Azure lifecycle and RC28's external platform overlay. Copy the existing OCI index
into a fresh ACR without rebuilding, create only the named disposable canonical data plane, seed the
two versionless Key Vault references outside Terraform, and invoke the stable qualification
entrypoint. An infrastructure mismatch may use the bounded two-attempt allowance only before Python
starts; candidate code gets one manual attempt and one replay after manual success.

## Implementation Notes

- The committed configuration hash covers the complete candidate, provider, resource, data-plane,
  workload, attempt, and cleanup object using sorted compact JSON.
- Azure sign-in is current. The accepted Phase 6 PostgreSQL Keychain entry remains present, but its
  deleted server no longer resolves; this objective therefore binds a new disposable PostgreSQL 15
  flexible server rather than silently reusing stale state.
- A read-only SKU check found this subscription restricted from PostgreSQL provisioning in East US;
  the exact B1ms/32 GiB PostgreSQL 15 state server is therefore bound to available Central US while
  the Container Apps profile remains in East US.
- Snowflake account and operator coordinates are represented only by hashes. OAuth refresh and
  permission checks remain read-only preflights and must stop the lane before mutation if absent.
- This slice can close exact-candidate Azure correctness only. Cost remains `not_evaluated` until
  provider invoices post; Azure scale, pairwise, soak, public release, and support remain open.
- PR #353 merged as protected main `fdcf14d`; exact-main run `31964559562` passed all five jobs
  before any provider mutation. The objective was then consumed by one manual candidate attempt;
  its result and exact cleanup are tracked separately in DANDER-212.

## Review Log

### 2026-08-16 — PASS

The exact objective and sanitized coordinate hashes reached protected main and all five exact-main
jobs passed before mutation. This closes objective binding only; it does not imply a passing Azure
candidate result.
