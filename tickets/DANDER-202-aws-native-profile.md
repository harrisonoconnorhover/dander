---
id: DANDER-202
title: Implement the AWS-native canonical profile
status: in-code
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200]
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
- [ ] Protected CI and independent completion review pass for the runtime-overlay correction before
  a replacement qualification candidate is cut.

## Implementation Notes

- The selected version-2 manifest retains typed AWS Secrets Manager coordinates and permits only
  the named Redshift/PostgreSQL/Glue/AWS-Secrets composition or the accepted GCP composition.
- AWS-native secrets are full account-and-region-bound ARNs. The task role receives only declared
  secret reads, Redshift authentication, the configured wildcard-free staging prefix, and the
  configured Glue namespace. Terraform passes only a non-secret binding document; the runtime
  resolves values with the task role and removes them after execution.
- Independent review caught the unresolved-DSN, IAM-wildcard, and COPY-role partition defects
  before live use; focused runtime and Terraform regression contracts cover the corrections.
- Fargate retains the exact selected deployment separately from the shared platform profile, so
  runtime configuration remains unambiguous when staging and production reuse one profile.
- Local Python, Terraform validation, provider-mocked Terraform tests, independent review, PR
  checks, and exact-main CI pass at `fe325ff`. Live AWS qualification and support promotion remain
  separate open gates; RC21 is the first candidate cut after this implementation merged.
- Exact RC22 live preflight later exposed a packaging defect before the Fargate plan: its immutable
  image contains only the GCP and Kubernetes deployment coordinates, so the AWS-native command
  cannot resolve its selected deployment. The disposable data plane was removed without a task run.
- The local correction projects the validated, selected non-secret platform overlay through the
  task definition and materializes it only in runtime scratch space. The first completion review
  then caught missing self-scoped database egress, direct lookahead inside an open transaction, and
  an understated crossover threshold. Commit `8240bcfc3585b8217a607cb08d2d97290ca13afa`
  corrects all three with focused Python and Terraform checks and protected CI run `31863498217`;
  review rerun and a replacement source-free candidate remain open.
