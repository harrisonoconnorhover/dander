---
id: DANDER-231
title: Persist durable AWS Control run state
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-230]
created: 2026-08-25
---

## Context

DANDER-230 established provider-neutral hosted-run contracts and deterministic restart adoption.
Control now needs durable state before an AWS execution backend or always-on reconciler can use
those contracts. This ticket adds only the S3 persistence boundary and canonical record identity.

## Acceptance Criteria

- [x] Canonically serialize and deserialize `ExecutionPlan`, `RunRecord`, and `AttemptRecord` under
  explicit versioned schemas.
- [x] Compute `ExecutionPlan.revision` from its versioned canonical contents and reject persisted
  revision/content mismatches.
- [x] Store current run snapshots with S3 `If-None-Match` creation and `If-Match` compare-and-swap
  updates using opaque ETags.
- [x] Store attempt intent as create-only immutable objects, permitting only byte-identical replay.
- [x] Resolve idempotency by environment, project, and hashed caller key without persisting the
  caller's raw key; reject reuse for different submission contents.
- [x] Return bounded deterministic run pages through an opaque exclusive cursor.
- [x] Recover after a restart between the durable idempotency claim and initial snapshot creation.
- [x] Preserve the existing direct launcher and single-container runtime paths.

## Design

`ExecutionPlan.revision` is no longer constructor input. It is the SHA-256 of a canonical `/v1`
envelope containing the selected graph, immutable image, backend/profile identity, execution
template, deadline, and retry policy. Deserialization reconstructs the plan, recomputes the digest,
and rejects either mismatch or non-canonical bytes.

`S3RunStore` uses three deterministic object families below one caller-bound prefix:

- `runs/{run_id}.json` is the current canonical snapshot and uses its S3 ETag as the opaque CAS
  revision.
- `attempts/{run_id}/{attempt_id}.json` is immutable attempt intent created with
  `If-None-Match: *`.
- `idempotency/runs/{environment}/{project}/{key_sha256}.json` owns the scoped claim and embeds the
  pristine initial snapshot needed to repair a crash before the run object exists.

Reads are size-bounded and pinned to the ETag returned by `HEAD`. Conditional conflicts are mapped
to provider-neutral run-store errors. A replay of an existing claim returns the durable snapshot;
if that snapshot is absent, the adapter recreates it from the immutable claim before returning.

## Boundaries

- No S3 bucket, IAM policy, Fargate call, queue, scheduler, reconciler, or live provider execution is
  added here.
- No hosted lifecycle is wired into `dander control serve`; DANDER-232 and DANDER-233 remain the
  backend and composition tickets.
- No Spark, Kubernetes, dynamic cluster sizing, generalized autoscaling, or alternative cloud
  backend is introduced.
- S3's general-purpose bucket conditional-write and strong read-after-write behavior is the storage
  primitive; cross-object recovery is explicit rather than presented as a transaction.

## Review Log

_Awaiting protected PR review._
