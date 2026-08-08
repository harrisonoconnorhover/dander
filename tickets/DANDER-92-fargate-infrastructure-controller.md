---
id: DANDER-92
title: Provision a bounded Fargate execution controller
status: in-review
component: deployment
epic: cloud-portability
depends_on: [DANDER-90, DANDER-91]
created: 2026-08-08
---

## Context

The Fargate provider can build a safe execution projection and prepare keyless runtime identity,
but no product Terraform stack yet turns that projection into an executable, observable lifecycle.
This slice provisions the AWS resources without exposing them through public CLI lifecycle commands
or claiming support.

## Acceptance Criteria

- [x] Use a separate AWS Terraform root with an operator-configured encrypted S3 backend.
- [x] Create an immutable, scan-on-push ECR repository and one ECS cluster.
- [x] Create non-root, read-only task definitions from exact execution projections.
- [x] Keep the image-pull/log execution role separate from each runtime task role.
- [x] Grant AWS secret access only for exact declared ARNs.
- [x] Enforce one absolute deadline through a Standard Step Functions ECS `.sync` controller.
- [x] Retry only runtime exit code 75 within the declared launcher-attempt bound.
- [x] Keep Scheduler delivery retry state separate and honor paused projections.
- [x] Route exhausted, failed, timed-out, and aborted outcomes to encrypted failure targets.
- [x] Package every clean AWS Terraform asset and keep Fargate outside the support manifest.
- [x] Focused tests, Terraform validation, provider-mocked plan, and read-only AWS plan pass.
- [x] Full validation and isolated GCP no-drift pass.
- [ ] Protected CI passes.

## Design

Use one state machine and task definition per pipeline. EventBridge Scheduler calls the universal
Step Functions `StartExecution` target with only non-secret schedule context. The optimized ECS
integration observes and best-effort stops its own tasks. Direct CLI lifecycle and live parity stay
in later, separately reviewed tickets.

## Review Log

The read-only plan proposed 23 creates, zero changes, and zero destroys against AWS account
`184463061564` in `us-east-1`. No Terraform apply or AWS resource mutation occurred.
