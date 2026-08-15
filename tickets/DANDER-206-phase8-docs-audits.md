---
id: DANDER-206
title: Complete Phase 8 operations docs and audits
status: open
component: docs
epic: cloud-portability-phase-8
depends_on: [DANDER-202, DANDER-203, DANDER-205]
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
  scans and the complete audit against the final source-free candidate remained open at that point.
- Exact private RC22 then passed protected CI run `31825533602`, including all five required jobs.
  The local exact-artifact repeat passed clean wheel/source installs, full runtime import,
  Terraform/Helm, dependency and Git-history secret audits, rootless read-only runtime checks, and
  HIGH/CRITICAL Trivy scans of infrastructure, the main image, and the OCI controller image. The
  normalized record is `docs/evidence/phase8/2026-08-14/rc22-local-audit.json`.
- Private arm64 RC23 passed local release/control metadata, clean wheel/source runtime-all installs,
  rootless read-only execution, 1,708 tests, dependency/source-secret audits, and HIGH/CRITICAL
  Trivy image/infrastructure scans. Its local PostgreSQL run observed equal DIRECT/COPY rows, but
  completion review invalidated the byte-threshold objective. RC23 is unprotected and not
  multi-platform, so the final-candidate audit and crossover criteria remain open.
- This ticket remains open for profile-specific operator documentation and the final honest status
  reconciliation after DANDER-204 and DANDER-205; the completed audit does not promote support.
- The AWS-native profile now has one explicit experimental runbook covering prerequisites,
  non-root/keyless identity, typed configuration, network and secret boundaries, saved-plan
  deployment, operations, upgrade, rollback, exact cleanup, and troubleshooting. It records the
  RC22 packaging defect and replacement-candidate gate rather than promoting the profile.
