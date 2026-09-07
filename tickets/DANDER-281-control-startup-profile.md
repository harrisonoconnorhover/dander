---
id: DANDER-281
title: Integrate PostgreSQL Control with one startup profile
status: done
component: python
depends_on: [DANDER-257]
created: 2026-09-07
---

## Acceptance Criteria

- [x] Integrate existing PostgreSQL Control storage and typed startup independently of HDFS.
- [x] Retain AWS startup behavior and the existing execution backends.
- [x] Add one YAML profile using existing CLI option validation and explicit-flag precedence.
- [x] Resolve profile paths relative to the profile and document a runnable local example.
- [x] Pass focused CLI/startup tests and real disposable-PostgreSQL conformance.
- [x] Merge through protected checks and verify exact-main CI.

## Verification

[PR #528](https://github.com/harrisonoconnorhover/dander/pull/528) merged as `9fdbaa3`.
All six [exact-main checks](https://github.com/harrisonoconnorhover/dander/actions/runs/34151542113)
passed, following the local 2,280-test suite and real disposable-PostgreSQL validation.

## Boundary

One Control process per run-store schema. No Hadoop execution, provider provisioning, source
data-store migration, or support promotion. The preserved HDFS branch remains separate.
