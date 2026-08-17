---
id: DANDER-217
title: Bind the RC29 Azure canonical correctness objective
status: in_progress
component: infrastructure
epic: cloud-portability-phase-8
depends_on: [DANDER-214, DANDER-216]
created: 2026-08-17
---

## Context

Private RC29 is the protected source-free replacement for the deterministic RC28 Snowflake
identifier defect. RC28 remains immutable and its failed execution remains evidence. One new exact
objective must bind RC29, a fresh disposable namespace, execution limits, cost, and cleanup before
the materially affected Azure correctness lane may rerun.

## Acceptance Criteria

- [x] One objective binds RC29, its immutable GAR/ACR digest, and the named Azure canonical profile.
- [x] One manual run and one success-conditional replay use the packaged synthetic scalar fixture.
- [x] Fresh Azure, PostgreSQL, and Snowflake coordinates, secret references, retry limits, maximum
  lifetime, exact cleanup, and a USD 2 ceiling are fixed before mutation.
- [x] Private coordinates must match committed hashes in a read-only preflight, while secret values
  remain outside Git and prior failed evidence remains unchanged.
- [ ] Protected review and exact-main CI pass before any Azure, Snowflake, or PostgreSQL mutation.

## Design

Copy the accepted RC29 OCI index into a fresh ACR without rebuilding, create only the named
disposable canonical data plane, seed two versionless Key Vault references outside Terraform, and
invoke the stable qualification entrypoint. Infrastructure may use the bounded two-attempt
allowance only before Python starts; candidate code gets one manual attempt and one replay after
manual success. Automatic retries remain disabled.

## Implementation Notes

- The configuration hash covers the full candidate, provider, resource, workload, attempt, and
  cleanup object using sorted compact JSON; budget allocation remains explicit beside it.
- Read-only Azure checks confirmed the new resource group is absent and the ACR, storage-account,
  Key Vault, and PostgreSQL server names are globally available. Central US remains the bound
  PostgreSQL region while the Container Apps profile remains in East US.
- Azure ActualCost currently shows USD 0.0052728924 for August 16. Delayed posting keeps the prior
  USD 2 bound and RC29 publication's USD 0.25 reserve held; this objective reserves another USD 2,
  leaving USD 5.75 unreserved under the additional aggregate ceiling.
- This branch performs no provider mutation. Exact RC29 publication evidence is protected at
  `5ed786d`; its exact-main CI and this objective's protected gates must pass before mutation.
