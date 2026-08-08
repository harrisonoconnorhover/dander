---
id: DANDER-93
title: Plan manifest-defined Fargate deployments
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-92]
created: 2026-08-08
---

## Context

The packaged Fargate Terraform root could be validated directly, but version-2 projects could not
select the launcher or drive a saved plan through Dander. This slice connects the existing
configuration, projection, and Terraform boundaries without creating AWS bootstrap resources or
claiming live support.

## Acceptance Criteria

- [x] Accept a validated Fargate launcher block in a version-2 deployment.
- [x] Preserve the complete selected launcher configuration after project resolution.
- [x] Select only an explicitly named deployment for AWS planning.
- [x] Render the existing Fargate execution projections from the resolved manifest.
- [x] Require an immutable ECR digest in the selected AWS account and region.
- [x] Initialize an existing encrypted S3 backend with a DynamoDB lock table.
- [x] Save a plan without applying and print exact review/apply commands.
- [x] Apply only a previously saved plan after explicit confirmation.
- [x] Preserve version-1 and Cloud Run behavior.
- [x] Full local validation and retained GCP no-drift pass.
- [ ] Protected CI passes.

## Design

Keep the GCP bootstrap unchanged. Add one AWS-specific bootstrap adapter and two CLI commands that
consume the shared resolved project and launcher registry. AWS state/ECR creation, image
publication, and runtime operations remain separate follow-up slices.

## Implementation Notes

Terraform 1.9 does not support the newer S3 `use_lockfile` backend option, so this lifecycle uses
an explicitly named DynamoDB lock table and retains the existing Terraform 1.9+ contract.

## Review Log

No AWS or GCP apply occurred. The new AWS account was inspected read-only; it currently has a
default VPC but no S3 state bucket or DynamoDB lock table. A credentialed, local-backend Terraform
plan reported 23 additions, 0 changes, and 0 destructions. The retained GCP project reported exactly
`No changes.`
