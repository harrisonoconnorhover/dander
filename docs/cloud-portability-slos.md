# Cloud Portability Qualification Objectives

These are release gates for a named Dander platform profile, not a claim that every cloud/service
combination is supported. GCP/Cloud Run/BigQuery is the initial compatibility profile. A new profile
publishes its measured results, image digest, provider regions and service shapes before its status
can change from unsupported or experimental to supported.

## Hard correctness objectives

- Deterministic conformance fixtures produce exactly equal normalized rows and schema across every
  warehouse claimed by the profile. No mismatch is tolerated.
- Inclusive cursor replay, duplicate launcher delivery, and a crash after destination commit but
  before cursor commit create zero duplicate business keys and never regress a cursor.
- Controlled overlap produces one owner, a truthful skipped outcome for the loser, and zero stale
  publications. Every writer mode must reject an old fencing token after a newer claim.
- Every catchable execution produces one sanitized terminal run record. SIGKILL is reconciled by
  the next successful lease owner and can never leave a successful terminal record.

## Bounded operation objectives

- The bounded-memory benchmark uses input at least ten times larger than the container memory limit
  and must complete without exceeding 80% of that limit after warm-up. The configured batch size,
  row width, schema depth, and observed peak resident memory are part of the result.
- Run-scoped staging is deleted on handled completion or failure. Staging left by process death has
  a provider-enforced expiration of no more than 24 hours.
- A retryable provider failure exhausts a documented finite retry budget, emits a stable failure
  code, and leaves the next invocation replay-safe. No operator edits leases, cursors, or staging.
- A scheduled run must finish within its configured hard deadline and before its next scheduled
  occurrence. The provider-side launcher, not the container alone, enforces that deadline.

## Service and cost evidence

- During qualification, at least 99% of scheduled executions unaffected by an intentionally
  revoked credential or a documented source outage finish successfully, and 100% of failures are
  visible in run history and provider logs. Fewer than 100 qualifying runs is reported as a sample,
  not represented as statistical proof.
- Bulk, incremental, transform, and concurrent benchmarks record rows, bytes, duration, peak
  memory, provider work metrics, and estimated cost. There is no universal throughput or cost
  promise until measured evidence supports one.
- Paid scale tests require a separately approved ceiling. A provider budget or billing alert is
  evidence and notification, never described as a hard spending cap. The default unapproved paid
  test budget is $0; every live proof records its separately approved ceiling before mutation.

## Promotion rule

A profile is supported only when all hard correctness objectives pass on the exact release
candidate and the benchmark report documents every bounded-operation objective. Failed or missing
evidence leaves the profile experimental or unsupported; it does not weaken the objective.
