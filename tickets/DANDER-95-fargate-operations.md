---
id: DANDER-95
title: Operate and verify manifest-bound Fargate pipelines
status: in-review
component: deployment
epic: cloud-portability
depends_on: [DANDER-94]
created: 2026-08-08
---

## Context

The packaged Fargate controller had plan/apply commands but no provider-native operator surface.
Operators need exact, manifest-bound start, status, logs, cancellation, replay, and verification
without copying AWS resource identifiers by hand or exposing provider response details.

## Acceptance Criteria

- [x] Resolve one exact Fargate deployment and pipeline from the project manifest.
- [x] Start and replay only after explicit CLI confirmation.
- [x] Normalize execution state and correlate CloudWatch logs to the exact ECS task.
- [x] Reject execution ARNs that do not belong to the selected pipeline.
- [x] Cancel only running controller executions.
- [x] Verify the controller, cluster, schedule, task image, log group, and immutable ECR policy.
- [x] Scope deployment-role operation permissions to Dander state machines, executions, and logs.
- [x] Keep Fargate unsupported until source-free live acceptance passes.
- [x] Protected CI passes.

## Design

Use the AWS CLI under the existing short-lived deployment profile. Derive every resource name from
the validated manifest and the same deterministic naming rule as Terraform. Return small sanitized
records rather than unrestricted AWS responses. Step Functions remains the lifecycle authority;
replay starts a fresh execution at Dander's inclusive watermark boundary.

## Review Log

No AWS or GCP apply is part of this slice. A credentialed AWS stage-zero plan and retained GCP
no-drift plan are required before merge.
