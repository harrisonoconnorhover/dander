---
id: DANDER-230
title: Define Control hosted-run orchestration contracts
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-121]
created: 2026-08-25
---

## Context

Control already exposes optional hosted run operations, while the current runtime and direct CLI
launchers execute one disposable Dander container. The next milestone makes Control the always-on
authority for hosted runs without making it mandatory for local, diagnostic, emergency, or
non-hosted operation. This ticket establishes the provider-neutral contract before any durable
store, cloud SDK, scheduler, reconciler, or live execution is added.

## Acceptance Criteria

- [x] Replace lifecycle start input with a resolved `RunSubmission` containing environment,
  immutable plan identity, trigger occurrence, idempotency, request time, and optional deadline.
- [x] Separate immutable `ExecutionPlan` from independently versionable `TriggerSpec` scheduling.
- [x] Define `RunRecord` and immutable `AttemptRecord` with deterministic logical identities.
- [x] Keep execution progress, outcome, result availability, and cleanup confirmation as separate
  typed dimensions with monotonic transition rules.
- [x] Define provider-neutral `ExecutionBackend` and conditional `RunStore` protocols.
- [x] Require `submit_or_adopt(plan, run_id, attempt_id, trigger)` and document at-least-once
  requests with idempotent provider effects, not exactly-once execution.
- [x] Prove with a fake backend/store that restart after provider acceptance but before handle
  persistence adopts one provider effect and one immutable attempt.
- [x] Preserve the existing HTTP route as a compatibility entry point through a submission
  resolver, without wiring a hosted lifecycle into `dander control serve` yet.

## Design

`RunSubmissionResolver` owns environment and exact-plan selection for compatibility requests. The
resolved submission crosses `RunLifecyclePort.start` as one validated object. `ExecutionPlan`
contains graph revision/SHA, immutable image, profile, backend, execution template, deadline, and
bounded retry policy; it rejects an embedded schedule. `TriggerSpec` contains schedule, time zone,
dependency, enabled state, and the exact selected plan revision.

Durable run snapshots use `run_state`, `outcome`, `results_state`, and `cleanup_state`, so a
successful pipeline can truthfully expose available results and uncertain cleanup. Replay points to
its source but uses a new idempotency key and therefore a new logical run ID. Attempt IDs are
derived from logical run ID plus attempt number. A dispatch may repeat the backend request after a
crash, but the backend must create or adopt the one provider execution for that attempt.

## Implementation Notes

- The new contracts import no provider SDK and do not replace the existing single-container or
  direct CLI runtime path.
- The compatibility route has no default production resolver yet. Run operations remain honestly
  absent from `dander control serve` until lifecycle composition is implemented.
- No S3 keys, SQS behavior, Fargate API calls, EventBridge schedule, cloud resource, or live run is
  part of this ticket.
- Spark, Kubernetes orchestration, dynamic cluster sizing, generalized autoscaling, and horizontal
  Control reconciliation remain extension points rather than current implementations.

## Bounded Follow-on Sequence

1. **DANDER-231:** S3 run snapshots, conditional revisions, idempotency index, immutable attempts,
   pagination, recovery, and one active reconciler assumption.
2. **DANDER-232:** Fargate SDK backend with deterministic provider identity, adopt, observe, logs,
   cancel, and normalized handles; preserve direct launchers.
3. **DANDER-233:** Compose plans, store, backend registry, lifecycle, reconciler, compatibility
   route, readiness, and shutdown into `dander control serve`.
4. **DANDER-234:** Route schedules through encrypted SQS wakeups with DLQ and occurrence
   idempotency.
5. **DANDER-235:** One bounded AWS/Redshift vertical-slice acceptance covering API, schedule,
   cancel, exit-75 retry, restart adoption, results, and cleanup.
6. **DANDER-236:** Review the AWS-shaped interface, then separately authorize a GCP/BigQuery
   backend. Do not auto-start it.

## Review Log

_Awaiting protected PR review._
