---
id: DANDER-215
title: Refresh vulnerable container utility packages
status: done
component: python
epic: cloud-portability-phase-8
depends_on: []
created: 2026-08-17
---

## Context

Protected CI began rejecting the unchanged runtime image after Trivy learned about
CVE-2026-53615 in Debian's util-linux package family. The pinned Python base image still carries
version `2.41-5`, while Debian publishes the fixed `2.41.5-0+deb13u1` packages.

## Acceptance Criteria

- [x] The repository and generated-project images install the fixed util-linux package family from
  the active Debian repository without changing the pinned base image.
- [x] The runtime container starts, its qualification entrypoint passes, and Trivy reports no HIGH
  or CRITICAL fixed vulnerability.
- [x] Focused scaffold/runtime tests and proportionate repository checks pass.
- [x] Protected review and exact-main CI pass before the Phase 8 evidence and implementation lanes
  resume protected integration.

## Design

Extend the existing `apt-get update` and runtime dependency installation layer with the affected
util-linux binary packages. Apply the same package set to the root runtime and generated-project
Dockerfiles so new projects do not inherit the known vulnerability.

## Implementation Notes

- The failure is deterministic and unrelated to the PR #359 documentation diff.
- Debian resolves all affected packages to `2.41.5-0+deb13u1` from the current repository.
- The local runtime image, CLI, and qualification entrypoint pass; Trivy 0.69.1 reports zero
  HIGH/CRITICAL findings after the targeted refresh.

## Review Log

- 2026-08-17 — PASS: PR #361 merged as protected main `8d6719c`; exact-main CI run
  `31986274883` passed all five jobs.
