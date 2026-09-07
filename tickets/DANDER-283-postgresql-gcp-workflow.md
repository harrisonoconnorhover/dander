---
id: DANDER-283
title: Exercise a real GCP pipeline through PostgreSQL Control
status: in-code
component: python
depends_on: [DANDER-281]
created: 2026-09-07
---

## Context

PostgreSQL Control starts independently of AWS, but its Google clients still require Fargate
identity. Complete one real workflow and make its setup reproducible before widening support.

## Acceptance Criteria

- [x] Use Google Application Default Credentials outside configured AWS federation.
- [x] Preserve strict federation selection and sanitize credential errors.
- [x] Run the public Greenhouse graph through PostgreSQL-backed Control and inspect its output.
- [x] Restart Control during execution, adopt the same provider execution, and retain run history.
- [ ] Exercise cancellation and verify the provider reaches a terminal state.
- [ ] Provide a runnable profile preparation example and document observed limits.
- [ ] Merge implementation through protected checks and verify exact-main CI.

## Execution Scope

After protected merge and exact-main CI, build one immutable worker from that main commit. Use
the documented `dander-proof-harrison-20260801` project, `us-central1`, existing Artifact Registry
repository `dander`, and existing `dander-runtime-graph` identity. Create one temporary
`dander-control-pg-greenhouse` Job for `greenhouse_jobs_graph`; do not change the retained jobs or
resume any provider schedule. Use one task, one vCPU, 512 MiB, a 600-second timeout and no launcher
retries. Control submits one recovery/output run and one cancellation run. Any corrected attempt
must first reconcile its predecessor under the standing repository retry policy.

The owner is this task. Reserve USD 1 for execution, image storage, logs, and bounded BigQuery work,
plus USD 2 for delayed billing, within the USD 25 aggregate monthly cash ceiling. The live September
7 billing account showed USD 0.40 month-to-date and no active promotional credits. Recheck cost
and concurrent reservations before execution. Stop the local Control process and remove its
disposable PostgreSQL container, temporary Job, and test image after observation, within 24 hours;
retain sanitized results locally. No new service account, IAM grant, paid cluster, or public release.

## Verification

The initial identity patch passed 44 focused identity/backend tests, Ruff lint/format, and strict
typing across 492 files. Live acceptance remains pending; existing RC22 completion events do not
contain the telemetry required by current Control.

The identity patch merged in PR #531 as `a548d74`; all six exact-main checks passed. Its immutable
worker completed the real graph after a forced Control restart. The idempotent API request reused
the same run and provider execution. Ingestion processed 18 jobs, and the graph's 38 published
rows matched the stored source exactly, with 38 distinct keys and no missing or unexpected rows.

The second execution was canceled by Google before task startup. Google omitted `completionTime`,
leaving the initial Control implementation stuck in `canceling`. The regression failed before
the correction and now passes; 38 focused backend/lifecycle tests, including PostgreSQL recovery,
Ruff, and strict typing pass. A read-only observation of that exact provider execution now returns
terminal/canceled/confirmed. Protected merge and durable API reconciliation remain pending.
