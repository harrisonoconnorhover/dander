---
id: DANDER-283
title: Exercise a real GCP pipeline through PostgreSQL Control
status: done
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
- [x] Exercise cancellation and verify the provider reaches a terminal state.
- [x] Provide a runnable profile preparation example and document observed limits.
- [x] Merge implementation through protected checks and verify exact-main CI.

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
typing across 492 files. Existing RC22 completion events lack the telemetry required by current
Control, so the live workflow used a temporary worker built from the merged identity patch.

The identity patch merged in PR #531 as `a548d74`; all six exact-main checks passed. Its immutable
worker completed the real graph after a forced Control restart. The idempotent API request reused
the same run and provider execution. Ingestion processed 18 jobs, and the graph's 38 published
rows matched the stored source exactly, with 38 distinct keys and no missing or unexpected rows.

The second execution was canceled by Google before task startup. Google omitted `completionTime`,
leaving the initial Control implementation stuck in `canceling`. The regression failed before
the correction and now passes; 38 focused backend/lifecycle tests, including PostgreSQL recovery,
Ruff, and strict typing pass. A read-only observation of that exact provider execution now returns
terminal/canceled/confirmed.

[PR #531](https://github.com/harrisonoconnorhover/dander/pull/531) passed all six
[exact-main checks](https://github.com/harrisonoconnorhover/dander/actions/runs/34158539704)
at `a548d7418fa7c125b71195a27d2868e05503c80e`. The cancellation correction and preparation example
merged through [PR #532](https://github.com/harrisonoconnorhover/dander/pull/532); all six
[exact-main checks](https://github.com/harrisonoconnorhover/dander/actions/runs/34161045039)
passed at `98d4186b376fc2458bb33ce7173311eb7f0467f0`.

Control on that final revision reconciled the existing canceled execution without a new worker.
Both terminal API histories survived another restart with identical results. Exactly two provider
executions existed: the successful recovery run and the canceled run. The completion event was
visible through paginated API logs, including empty pages with a continuation cursor. Preparing
the same profile twice reused its graph and plan; 15 focused profile/identity tests also passed.

No active owned lease or temporary staging table remained. The temporary Cloud Run Job and image,
local Control process, PostgreSQL container, and temporary database credentials were removed and
their absence verified. All five retained job images and latest execution identities stayed
unchanged; all five schedules remained paused. A database snapshot and operator results are local
under `tmp/postgresql-gcp-20260907`, outside version control. The conservative cost reservation
included observed AWS charges and uncertain other-provider storage within the monthly ceiling;
it is not a final billing measurement.

## Boundary

This verifies one public Greenhouse graph, native Google credentials, one local Control process,
and PostgreSQL 17. It does not qualify a hosted or multiple-replica topology, secure Hadoop, or a
new release. Ingestion processed 18 rows; the stored source retained 38 rows. Operation-level
telemetry was empty on this legacy graph path, so zero byte counters are not evidence of zero
warehouse cost. Broader release gates remain in DANDER-207.
