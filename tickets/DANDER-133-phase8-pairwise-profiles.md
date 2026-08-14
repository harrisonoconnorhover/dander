---
id: DANDER-133
title: Execute the Phase 8 pairwise profile matrix
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-130, DANDER-131, DANDER-132]
created: 2026-08-13
---

## Context

Adapter conformance and earlier lifecycle proofs do not establish the exact-candidate pairwise and
canonical profile gate.

## Acceptance Criteria

- [ ] Every supported-target case in the deterministic Phase 8 matrix passes on one candidate.
- [ ] Unsupported OCI-to-GCP and non-AWS-to-AWS-service boundaries continue to fail closed.
- [ ] Equal canonical output, identity refresh where applicable, provider cleanup, and retained-GCP
  no drift are recorded.
- [ ] No unlisted Cartesian combination inherits a support claim.
