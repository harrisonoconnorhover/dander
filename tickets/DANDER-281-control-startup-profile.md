---
id: DANDER-281
title: Integrate PostgreSQL Control with one startup profile
status: in-review
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
- [ ] Merge through protected checks and verify exact-main CI.

## Boundary

One Control process per run-store schema. No Hadoop execution, provider provisioning, source
data-store migration, or support promotion. The preserved HDFS branch remains separate.
