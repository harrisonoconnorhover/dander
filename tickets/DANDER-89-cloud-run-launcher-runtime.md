---
id: DANDER-89
title: Route Cloud Run projection through the launcher provider boundary
status: completed
component: deployment
epic: cloud-portability
depends_on: [DANDER-88]
created: 2026-08-08
---

## Context

The shared provider registry declared launcher support, but Terraform bootstrap still constructed
Cloud Run templates directly. Cloud Run must use the provider boundary without changing any
accepted template value, Terraform address, or live resource.

## Acceptance Criteria

- [x] Version 1 and migrated version 2 projects retain Cloud Run selection.
- [x] The Cloud Run implementation loads only after launcher selection.
- [x] Terraform bootstrap obtains execution templates from the selected launcher runtime.
- [x] Provider output matches the accepted Cloud Run projector exactly.
- [x] An unknown launcher fails before Terraform runs.
- [x] Existing direct projection APIs remain compatible.
- [x] Focused launcher, bootstrap, project, and projection tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Add one small `LauncherRuntime` containing the existing execution-template construction protocol
and declared launcher capabilities. Register Cloud Run lazily and delegate to the proven GCP
projector. Keep Terraform modules, resource addresses, schedules, IAM, and runtime commands intact.

## Review Log

Full local validation, isolated retained-project no-drift, and protected CI passed. PR #120 merged
as `2d8837d4a59c45c9b33b3ca211c9303b8fb1a16f`.
