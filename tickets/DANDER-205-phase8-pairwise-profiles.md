---
id: DANDER-205
title: Execute the Phase 8 pairwise profile matrix
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-202, DANDER-203, DANDER-204]
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

## Implementation Notes

- The 2026-08-14 credential preflight is non-mutating: Azure requires interactive Entra
  reauthentication, the AWS session is expired, and OCI has no complete CLI profile. No paid or
  live pairwise run was started for those providers.
- Continue local and retained-GCP gates independently. Do not weaken or mark the unavailable
  provider cases passed; the sanitized blocker record contains no account or credential material.
