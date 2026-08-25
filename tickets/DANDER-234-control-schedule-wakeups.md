---
id: DANDER-234
title: Route hosted schedules through Control
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-233]
created: 2026-08-25
---

## Context

DANDER-233 made one always-on Control process the durable authority for API-submitted hosted runs.
Scheduled runs now need to enter that same lifecycle without re-enabling the existing direct
Fargate schedules, changing pipeline logic, or creating a second execution path.

## Acceptance Criteria

- [x] Canonically serialize scheduled `TriggerSpec` configuration and one versioned schedule
  wakeup containing trigger id, exact plan revision, and UTC scheduled occurrence.
- [x] Project EventBridge Scheduler context substitution into a standard SQS queue encrypted with
  SSE-SQS, with bounded delivery retry and an encrypted DLQ.
- [x] Derive occurrence idempotency from canonical trigger id, plan revision, and scheduled time,
  so Scheduler retries, SQS redelivery, and Control restart converge on one durable logical run.
- [x] Long-poll a bounded batch in Control and delete a message only after the existing lifecycle
  accepts its durable handoff; leave malformed or failed messages for redrive.
- [x] Reject unknown, disabled, stale-plan, missing-graph, and graph-revision-drift occurrences
  before provider dispatch.
- [x] Give Scheduler only `sqs:SendMessage` to the wakeup queue and DLQ, give Control only receive,
  delete, and attribute reads on the wakeup queue, and scope Scheduler trust to the exact account
  and default schedule group.
- [x] Inject canonical plans/triggers through the existing ephemeral Control config volume, include
  the queue consumer in readiness and graceful shutdown, and retain one Control task.
- [x] Add deterministic renderer, read-only live-verifier, Terraform, IAM, CLI, consumer, and
  serialization tests.
- [x] Preserve direct CLI execution, the existing paused Fargate launcher schedule, and the single
  Dander worker container.

## Design

Each schedule targets one standard SQS queue with a canonical body whose only provider-resolved
field is `<aws.scheduler.scheduled-time>`. Scheduler delivery retry is capped at three attempts for
one hour. The source queue uses 20-second long polling, a 120-second visibility timeout, and moves a
message to the shared encrypted DLQ after five receives. Scheduler delivery failures also use that
DLQ; Control never consumes the DLQ.

`ScheduledRunSubmissionResolver` validates the configured `TriggerSpec`, exact retained
`ExecutionPlan`, and current GraphStore record, then creates the same `RunSubmission` consumed by
API starts. The durable run store and Fargate backend remain authoritative for dispatch, retry,
status, results, cancellation, and cleanup. Scheduler attempts, queue deliveries, and launcher
attempts remain separate counters.

The AWS profile reuses its versioned GraphStore bucket for run snapshots under disjoint object
families. Non-secret canonical plans and triggers are written by the existing config-init
container; ambient task identity supplies S3, SQS, and Fargate authority. Terraform owns schedule,
queue, DLQ, policies, and their removal.

## Boundaries

- No live AWS/Redshift run or acceptance workload is executed; that remains DANDER-235.
- No GCP execution backend or BigQuery work is added; DANDER-236 remains separately authorized.
- No Spark, Kubernetes orchestration, dynamic sizing, generalized autoscaling, or horizontal
  Control is introduced.
- DANDER-234 implements at-least-once delivery with durable idempotency, not exactly-once delivery.

## Review Log

_Awaiting protected PR review._
