---
id: DANDER-233
title: Compose the hosted Control run lifecycle
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-231, DANDER-232]
created: 2026-08-25
---

## Context

DANDER-231 made Control run state durable, and DANDER-232 implemented the existing Fargate
controller as a hosted execution backend. Control now needs the smallest always-on composition that
selects an immutable plan, dispatches or adopts a worker, reconciles durable truth, and exposes the
already accepted run routes. The existing single Dander container remains the worker.

## Acceptance Criteria

- [x] Select one active canonical execution plan by environment, project, graph, and exact graph
  revision while retaining plan-revision lookup for durable runs.
- [x] Select execution providers through a provider-neutral backend registry; prove another backend
  ID uses the same lifecycle without changing graph or pipeline logic.
- [x] Implement the existing `RunLifecyclePort` for start, list, get, logs, cancel, and replay.
- [x] Recover queued runs after restart by reusing immutable attempt identity and
  `submit_or_adopt`, without retaining the caller's raw submission key.
- [x] Reconcile progress, outcome, warehouse-result availability, and cleanup through conditional
  durable transitions.
- [x] Persist cancellation idempotency claims and original normalized responses in S3 so retries,
  conflicts, and the claim-before-provider-effect crash survive restart.
- [x] Run one bounded background reconciler, become ready only after a complete recovery sweep, and
  stop it before closing provider transports and durable state.
- [x] Wire the lifecycle, resolver, readiness, canonical plan files, and S3 run-store binding into
  the existing optional `dander control serve` path.
- [x] Require existing direct Fargate schedules to remain paused while Control owns hosted runs.
- [x] Preserve Control without run configuration, the direct AWS CLI, and the single-container
  runtime unchanged.

## Design

`ExecutionPlanRegistry` owns immutable plan lookup and compatibility-route selection.
`ExecutionBackendRegistry` owns only provider selection and transport shutdown. `ControlRunLifecycle`
composes those registries with the existing `RunStore`, `GraphStore`, transition functions, and
backend contracts. The background reconciler reads bounded store pages, dispatches or adopts queued
runs, observes active executions, repeats cancel requests idempotently, and continues terminal
cleanup reconciliation until confirmed.

The AWS startup path reads only canonical, content-addressed plan files. It derives exact Fargate
resource bindings from the existing validated project manifest, derives the expected S3 bucket
owner from that binding, and uses ambient SDK identity. Configuration and secret references remain
inside the immutable execution template/task definition; Control receives no secret values.

`dander control serve` remains backward compatible. Run routes are advertised only when at least
one `--execution-plan` and a `--run-store-bucket` are supplied. The same generic composition accepts
a future `gcp` backend mapping without changing lifecycle or pipeline logic.

## Boundaries

- No EventBridge Scheduler, SQS, DLQ, IAM, Terraform, or scheduled consumer is added; those remain
  DANDER-234.
- No live AWS/Redshift execution or acceptance workload is run; that remains DANDER-235.
- No GCP implementation or BigQuery acceptance is added; DANDER-236 remains separately reviewed.
- No Spark, Kubernetes orchestration, dynamic sizing, generalized autoscaling, or horizontal
  reconciler is introduced. The first deployment retains one active reconciler process.

## Review Log

_Awaiting protected PR review._
