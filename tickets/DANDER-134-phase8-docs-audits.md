---
id: DANDER-134
title: Complete Phase 8 operations docs and audits
status: open
component: docs
epic: cloud-portability-phase-8
depends_on: [DANDER-130, DANDER-131, DANDER-133]
created: 2026-08-13
---

## Context

Provider docs and protected CI exist, but final permissions, network, install, operations, upgrade,
rollback, troubleshooting, distribution, security, image, infrastructure, and dependency evidence
must describe the exact qualified profiles and candidate.

## Acceptance Criteria

- [ ] Each supported target has complete operator documentation and honest limitations.
- [ ] Full tests, typing, lint, packaging, distribution install, secret/dependency/image scans,
  Terraform validation/security, and Helm validation pass on the final candidate.
- [ ] Stale lifecycle and roadmap status is reconciled without erasing accepted evidence.

## Implementation Notes

- Commit `2d020d15fc52` passed the locally available distribution, dependency, Terraform, Helm, and
  container preflight on 2026-08-14. This is readiness evidence only: protected Trivy and secret
  scans and the complete audit against the final source-free candidate remain open.
