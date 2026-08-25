---
id: DANDER-232
title: Execute hosted Control runs through Fargate
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-231]
created: 2026-08-25
---

## Context

DANDER-230 defined provider-neutral hosted-run contracts, and DANDER-231 made their run and attempt
state durable. Control now needs one provider adapter for the existing AWS Fargate launcher before
the lifecycle, reconciler, or HTTP service can be composed. The existing Fargate state machine and
single Dander container remain the execution mechanism.

## Acceptance Criteria

- [x] Implement the provider-neutral `ExecutionBackend` contract with lazy AWS SDK clients and
  ambient hosted identity.
- [x] Bind only explicitly registered canonical plan revisions to their exact existing Fargate
  state machine, profile, pipeline, account, region, and immutable ECR image.
- [x] Derive one deterministic Standard Workflow execution name from logical run and attempt IDs.
- [x] Implement `submit_or_adopt` so restart, concurrent submission, and a lost start response
  converge on one provider execution.
- [x] Normalize Step Functions progress/outcome and selected-warehouse result availability without
  returning provider-native payloads.
- [x] Confirm cleanup only after the correlated ECS task reports `STOPPED`; otherwise preserve
  `pending` or `uncertain` independently of the known execution outcome.
- [x] Return bounded, cursor-paginated CloudWatch task logs and an empty page before a task exists.
- [x] Make cancellation idempotent and reconcile a stop race before returning an error.
- [x] Sanitize AWS failures and close injected or constructed SDK clients idempotently.
- [x] Preserve the existing direct AWS operations CLI and single-container runtime paths.

## Design

`FargateExecutionBackend` receives a map from canonical `ExecutionPlan.revision` to the existing
`FargateBinding`. On submission it first looks up the deterministic execution ARN. A confirmed
`ExecutionDoesNotExist` permits `StartExecution`; any start error is followed by one adoption read
to cover acceptance followed by a lost response. Existing execution input must match both the
canonical plan revision and logical attempt correlation before it can be adopted.

The SDK request carries only the existing controller input fields: plan revision, scheduled or
submission time, scheduler attempt, and a non-secret Control correlation. Runtime environment,
secret references, command, identity, and resource settings remain in the already reviewed task
definition selected by the immutable plan. The same container continues to write directly to its
selected warehouse, so a successful controller execution makes results available without a new
result transport.

Step Functions is authoritative for execution outcome. ECS `DescribeTasks` is authoritative for
ephemeral worker cleanup. CloudWatch log reads use only the task ARN recovered from bounded output
or reverse execution history and the binding's exact log group.

## Boundaries

- No lifecycle/reconciler/API composition is wired into `dander control serve`; that is
  DANDER-233.
- No EventBridge/SQS scheduling, IAM, Terraform, live AWS execution, or acceptance workload is
  added; those remain DANDER-234 and DANDER-235.
- No GCP backend, Spark, Kubernetes orchestration, dynamic sizing, or generalized autoscaling is
  introduced.
- The hosted SDK adapter is additive. Existing operator-facing Fargate CLI operations are
  unchanged.

## Review Log

_Awaiting protected PR review._
