---
id: DANDER-90
title: Project the BigQuery runtime onto Fargate
status: completed
component: deployment
epic: cloud-portability
depends_on: [DANDER-89]
created: 2026-08-08
---

## Context

Phase 1B proved immutable artifact copy and keyless Fargate-to-Google identity in isolation. The
first production-shaped step is a typed Fargate launcher projection that consumes the shared
execution contract without implying that infrastructure or lifecycle support already exists.

## Acceptance Criteria

- [x] Fargate configuration validates AWS account, region, network, architecture, and storage.
- [x] The implementation loads only after Fargate selection.
- [x] Templates use immutable ECR images and distinct AWS task-role identity.
- [x] BigQuery environment and GCP Secret Manager references contain no credential values.
- [x] CPU/memory pairs and Fargate resource limits fail closed.
- [x] Guarded-free-tier execution is rejected because its GCP billing preflight is unsupported.
- [x] Schedule, CloudWatch, `awsvpc`, architecture, and public-IP intent remain explicit.
- [x] Fargate remains absent from the supported runtime-capability manifest.
- [x] Focused projection and registry tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Register one dependency-light `FargateLauncherConfig` and lazy factory. Reuse the shared immutable
execution template, carry provider-specific intent only in declared extensions, and keep AWS
Terraform, scheduler/controller behavior, credential assembly, and live execution out of this PR.

## Review Log

Full local validation, isolated retained-project no-drift, and protected CI passed. PR #121 merged
as `cff311ce23d6b7ef9dbad8b743bb9a8ae8321ac4`.
