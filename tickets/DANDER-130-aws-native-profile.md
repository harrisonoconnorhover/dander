---
id: DANDER-130
title: Implement the AWS-native canonical profile
status: in-review
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-128]
created: 2026-08-13
---

## Context

Fargate lifecycle acceptance currently covers only the GCP data plane. The factory and Terraform
bootstrap reject a non-GCP profile, so AWS-native qualification cannot begin yet.

## Acceptance Criteria

- [x] One typed Fargate projection supports Redshift, PostgreSQL state, Glue, and AWS Secrets
  Manager without weakening the accepted GCP projection.
- [x] Terraform and operations remain manifest-bound, plan-first, least-privileged, and keyless.
- [x] Contract tests cover projection, secret binding, provider assembly, and fail-closed invalid
  compositions.
- [ ] Protected CI and independent completion review pass before a qualification candidate is cut.

## Implementation Notes

- The selected version-2 manifest retains typed AWS Secrets Manager coordinates and permits only
  the named Redshift/PostgreSQL/Glue/AWS-Secrets composition or the accepted GCP composition.
- AWS-native secrets are full account-and-region-bound ARNs. The task role receives only declared
  secret reads, Redshift authentication, the configured staging prefix, and the configured Glue
  namespace; no static AWS credentials are projected.
- Local Python, Terraform validation, and provider-mocked Terraform tests pass. Live AWS
  qualification and support promotion remain open, and no qualification candidate has been cut.
