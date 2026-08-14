---
id: DANDER-130
title: Implement the AWS-native canonical profile
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-128]
created: 2026-08-13
---

## Context

Fargate lifecycle acceptance currently covers only the GCP data plane. The factory and Terraform
bootstrap reject a non-GCP profile, so AWS-native qualification cannot begin yet.

## Acceptance Criteria

- [ ] One typed Fargate projection supports Redshift, PostgreSQL state, Glue, and AWS Secrets
  Manager without weakening the accepted GCP projection.
- [ ] Terraform and operations remain manifest-bound, plan-first, least-privileged, and keyless.
- [ ] Contract tests cover projection, secret binding, provider assembly, and fail-closed invalid
  compositions.
- [ ] Protected CI and independent completion review pass before a qualification candidate is cut.
