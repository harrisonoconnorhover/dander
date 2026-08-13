---
id: DANDER-122
title: Add GCS GraphStore
status: accepted
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Provide the first object-store implementation without leaking GCS semantics through the API.

## Acceptance Criteria

- [x] Use generation-match create/update/delete controls and bounded list pagination.
- [x] Pass the shared mock conformance and one separately approved live restart/conflict/cleanup proof.
- [x] Verify bucket binding, encryption/versioning policy, cleanup, and no drift without credentials,
      graph rows, plan, or state in evidence.

## Design

Provider coordinates and SDK types stay inside the adapter.

## Implementation Notes

- Added a lazily loaded `GCSGraphStore` behind one immutable bucket/prefix binding.
- Used generation-zero create, generation-matched replacement, delete fences, and generation-pinned
  bounded reads. Inclusive list offsets are skipped explicitly and each provider page is bounded.
- Added hashed create/delete journals. Pending creates are completed before later mutations, while
  delete ownership is installed in the graph's exact generation before removal.
- Added shared exact credential-field enforcement using recognized secret references only.
- Added `google-cloud-storage` to the `gcp` and `runtime-all` optional extras, not the base install.
- Ran one separately approved live attempt against the protected-main implementation. It passed
  create/read, restart persistence, exact create/delete replay, update, stale update/delete
  rejection, list projection, deletion, bucket policy, cleanup, and retained-infrastructure
  no-drift checks.
- Qualified protected-main source only. Public `dander-platform==0.9.0rc18` predates the GCS
  adapter and remains explicitly outside this evidence.

## Review Log

- Pre-implementation adversarial review required exact create replay after a later mutation,
  inclusive-offset pagination handling, generation-pinned byte-bounded reads, and shared exact
  credential-field enforcement. The implementation and focused regressions include all four.
- Completion review blocked the implementation PR on two material defects:
  - concurrent identical create/delete requests can split into one success and one conflict instead
    of converging on the same durable replay result;
  - ordinary GCS list pages download every full graph body instead of projecting validated summary
    metadata from the object listing.
- The follow-up correction added owned-conflict reload/convergence with deterministic concurrent
  same-key create/delete tests. It also added safe summary object metadata and proves ordinary list
  traversal performs zero graph-body downloads.
- The full corrected local suite passes. No third adversarial pass was requested because the
  enabled review control caps each task at the completed plan and completion passes.
- The live-harness pre-review passed. Its final review found two runbook blockers before any paid
  mutation: placeholder private Terraform inputs and a bucket-absence check that could confuse an
  API failure with absence. The run derived those inputs only in memory, verified their saved
  hashes, and required a successful bucket listing before accepting absence.
- Attempt `druff-d3-gcs-live-2026-08-13-attempt-1` used a USD 0.25 ceiling with no automatic paid
  workload rerun. The graph workflow passed once; every object version and the bucket were removed.
  A read-only permission-path correction then reproduced 28 stage-zero and 113 platform resources
  as no-ops without rerunning the paid workflow. The coordinate-free record is
  `docs/evidence/gcp/2026-08-13/druff-gcs-graph-store.json`.
