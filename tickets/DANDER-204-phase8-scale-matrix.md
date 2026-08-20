---
id: DANDER-204
title: Execute the approved Phase 8 scale matrix
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200, DANDER-202]
created: 2026-08-13
---

## Context

Historical PostgreSQL, Snowflake, and Redshift reports are correctness or regression evidence, not
the required exact-candidate provider scale and cost qualification. BigQuery lacks a normalized
scale report.

## Acceptance Criteria

- [ ] Approved provider-specific SLOs and paid ceilings exist before mutation.
- [ ] Correctness, bounded-memory, bulk, incremental, concurrency, transform, failure, crossover,
  and cost reports cover every first-class warehouse and launcher.
- [ ] Every report records exact artifact, provider, workload, job, cost, cleanup, and objective
  evidence without credentials or row data.
- [ ] Optimization occurs only for a measured failed SLO and retains canonical equality.

## Implementation Notes

- Exact private RC22 passed the local PostgreSQL bounded-memory and four-pipeline concurrency
  objective sets on PostgreSQL 15.18 over TLS. The externally enforced 256 MiB run processed
  2.7248 GB logical input with 176,734,208 bytes peak RSS and left no staging relations.
- The initial 192 MiB bounded-memory attempt exceeded the approved 80% RSS threshold without an
  OOM. It remains in the attempts ledger; the proportional 256 MiB retry is the passing report.
- Exact RC22 also passed the pre-approved local bulk class with 500,000 narrow and 200,000 wide
  COPY rows, and the incremental class with a 3,000-row delta against a 300,000-row target. Both
  left zero staging relations and removed their disposable TLS PostgreSQL schemas.
- The exact-candidate correctness fixture also matched its approved normalized SHA-256 before and
  after replay, then removed its disposable schema and staging relations.
- Exact RC22's transform class passed scan, join, aggregation, incremental merge, and 21 generic
  assertion executions over 100,000 facts and 100 dimensions. The initial harness-only seed
  failure remains in the attempts record; it did not execute candidate transform code.
- The PostgreSQL-specific failure class passed bounded pool exhaustion, terminated-connection
  replacement, recovered state operations, warehouse cancellation rollback, and cleanup. The
  connector and launcher failure cases remain assigned to their own profile gates.
- Seven PostgreSQL classes pass on exact protected RC22. RC22 cannot satisfy crossover because its
  PostgreSQL factory exposes COPY only; those reports remain accepted baseline evidence.
- Private arm64 RC23 observed exact COPY/DIRECT equality, selected-transport telemetry, cleanup,
  and USD 0 local cost across five sizes and five repetitions. Completion review invalidated its
  10-row/1,400-byte recommendation because it omitted writer-counted field-name bytes. The corrected
  harness derives 1,490 bytes; RC23's threshold objective remains invalid.
- Private multi-platform RC24 passed the committed corrected crossover objective against disposable
  TLS PostgreSQL 15.18. Both transports produced equal rows, but DIRECT lost at the first sampled
  size, so no contiguous DIRECT-winning prefix exists and the measured threshold remains disabled
  at zero. All seven objectives passed with exact cleanup and USD 0 local cost. The later AWS-native
  corrections required private RC27, so no RC24 benchmark transfers. Applicable RC27 reruns,
  hosted cost, other warehouses, and every first-class launcher remain open.
- The Kubernetes portable launcher passed normalized correctness, bulk, incremental, transform,
  and PostgreSQL-specific failure Jobs on kind 1.32.2 under its reviewed deadline, retry, CPU, and
  memory controls. Remaining launcher classes, hosted scale/cost, and soak stay open.
- AWS access is restored. The exact RC22 AWS-native correctness objectives and USD 3 allocation are
  committed before mutation. The first disposable data-plane plan applied and cleaned up exactly,
  but read-only candidate inspection found RC22 lacks the selected AWS deployment before a Fargate
  plan or execution. Private RC27 packages the reviewed runtime-overlay, Fargate identity, explicit
  Redshift staging-role grant, and Serverless startup corrections and passes candidate inspection;
  the exact RC27 manual/replay correctness result now passes with duplicate-free canonical output
  and exact cleanup. Provider-measured cost and AWS scale remain open.
- PR #338 merged the five RC27-bound Kubernetes objectives as protected main `6ff041f`; exact-main
  run `31942160724` passed before execution. Exact RC27 then passed all five accepted launcher-scale
  classes on named kind 1.32.2 arm64 with zero retries, exact identity/objective evidence,
  non-estimated USD 0 cost, zero Warning events, and exact cleanup. Remaining Kubernetes launcher
  classes, hosted scale/cost, and soak remain open, as do the other warehouse and launcher cells.
- PR #339 merged the five resulting reports as protected main `b73fafc`; exact-main run
  `31943674409` passed all five jobs. The next focused objective binds Kubernetes bounded memory to
  exact RC27 and the accepted passing RC22 retry workload: 2.6 million rows, 2.7248 GB logical
  input, a 256 MiB candidate limit, the 80% peak-RSS gate, 2 CPU, a 600-second deadline, zero
  retries, reporter-sidecar collection, and USD 0 local cost. Protected merge and exact-main CI
  must precede execution; the RC22 result does not transfer.
- PR #340 merged that objective as protected main `72a422e`; exact-main run `31944524241` passed all
  five jobs before execution. Exact RC27 passed with 176,115,712 bytes peak RSS, 20,127 rows/second,
  zero retries/restarts, zero Warning events, no database residue, USD 0 local cost, and exact
  cluster/tag cleanup. A runtime-path harness preflight failed before benchmark code and was
  isolated by recreating the owned cluster. Kubernetes concurrency and crossover remain open.
- PR #341 merged the bounded-memory evidence as protected main `f864a2b`; exact-main run
  `31945860151` passed all five jobs. The next focused objective reuses its exact protected
  2.6-million-row/256 MiB configuration for the accepted coupled script but approves only four
  independent 5,000-row pipelines, stale-fence rejection, throughput, cleanup, and USD 0 cost.
  Protected merge and exact-main CI precede execution; no prior concurrency measurement transfers.
- PR #342 merged the concurrency objective as protected main `7dc51f8`; exact-main run
  `31946605370` passed all five jobs before execution. Exact RC27 completed all four 5,000-row
  pipelines in 334.55 ms at 59,781.789 rows/second, rejected the stale fence, and passed TLS,
  reporter, zero-retry/restart/warning, database cleanup, cluster/tag cleanup, and USD 0 checks. A
  PostgreSQL storage preflight failed before the candidate Job existed and was isolated by
  recreating the owned cluster. Protected evidence review remains; crossover stays separate.
- PR #343 merged the concurrency evidence as protected main `bd7489d`; exact-main run
  `31948875002` passed all five jobs. The next focused objective binds Kubernetes crossover to exact
  RC27 and the corrected RC24 1/10/100/1,000/5,000-row COPY/DIRECT workload, but transfers no RC24
  result or threshold. It preserves five repetitions, SCD1 equality, the 1 MiB direct ceiling,
  exact cleanup, a 2 CPU/512 MiB rootless Job, TLS PostgreSQL 15.18, and USD 0 local cost.
- PR #344 merged that objective as protected main `4166afb`; exact-main run `31949803615` passed all
  five jobs before execution. Exact RC27 passed all seven objectives in one disposable kind 1.32.2
  arm64 Job. COPY and DIRECT remained canonically equal; DIRECT tied through 10 rows and lost at
  larger samples, yielding an environment-specific 10-row/1,490-byte measurement without changing
  a product default. The run recorded 61,110 rows in 2.433 seconds, 25,117.139 rows/second,
  177,549,312 bytes peak RSS, zero retries/restarts/Warning events, no database residue, USD 0 local
  cost, and exact cluster/tag cleanup. PR #345 merged the sanitized evidence as protected main
  `366ce8a`; exact-main run `31951009601` passed all five jobs.
- The next focused objective binds the same exact-RC27 2.6-million-row/2.7248-GB bounded-memory
  workload to one disposable zonal GKE Standard cluster. It preserves the 256 MiB candidate limit,
  unchanged 80% peak-RSS gate, 2 CPU, TLS PostgreSQL 15.18, 600-second deadline, zero candidate
  retries, reporter collection, and exact owned-resource cleanup. Its USD 0.50 run ceiling is
  inside the retained USD 0.75 GCP soak/final-audit allocation; provider billing must post before
  cost can pass. Protected merge and exact-main CI precede any GCP mutation.
- The first RC28 Azure objective binds one manual run and one success-conditional replay to the
  canonical Container Apps/Snowflake/PostgreSQL/no-catalog/Key-Vault profile. It fixes the private
  candidate, disposable provider coordinates, two infrastructure preflights, one manual candidate
  attempt plus one success-conditional replay, exact cleanup, and the full USD 2 Azure allocation.
  Protected merge and exact-main CI must precede any Azure, Snowflake, or PostgreSQL mutation;
  provider billing must post before cost can pass.
- The 2026-08-16 read-only provider-cost reconciliation found AWS Cost Explorer still denied,
  Azure actual-cost rows still empty, and no hosted-GKE charge visible in the retained GCP billing
  report. Rounded GCP daily subtotals cannot establish Phase 8 attribution. Exact aggregate spend
  and remaining headroom remain unknown with USD 0 unallocated, so all affected cost gates stay
  `not_evaluated` and no new paid objective may start.
- A later operator authorization adds a separate USD 10 incremental ceiling. The fresh RC28 Azure
  correctness retry reserves USD 2, records the newly readable AWS and still-empty Azure billing
  baseline, and requires the corrected Snowflake staging-authority preflight before its one manual
  run and success-conditional replay. It does not transfer the failed attempt or qualify Azure
  scale/pairwise classes.
- PR #358 merged that retry objective as protected main `c4ad281`; exact-main run `31981210288`
  passed before mutation. All provider and canonical preflights passed, but the one manual RC28 run
  failed deterministically before publication because Snowflake uppercased unquoted portable
  logical identifiers against Dander's quoted lowercase source columns. Replay and automatic retry
  did not run. Exact cleanup passed and provider cost remains pending under the full USD 2 bound.
  PR #360 protected DANDER-214 at main `a2b72f8`; exact-main run `31987252875` passed all five
  jobs, so a replacement candidate now precedes the materially affected Azure rerun.
- PR #364 merged the RC29 Azure objective as protected main `46199fe`; exact-main run `31991302574`
  passed all five jobs before mutation. The manual execution and replay passed exact normalized
  Snowflake readback with no retries, and active Azure/Snowflake cleanup completed. Qualification
  still failed because the disposable resource group lived about 431 minutes against the committed
  120-minute maximum. RC29 needs a fresh orchestration-corrected objective, not a new candidate;
  provider cost remains pending under the held USD 2 bound.
- The fresh RC29 lifetime retry objective uses new `r29c` provider names and the same immutable
  digest. It requires Snowflake interactive verification before the resource clock, the scoped
  token before Azure provisioning, cleanup start by minute 75, and immediate teardown on any later
  interactive blocker. Its new USD 2 bound leaves USD 3.75 unreserved; protected merge and
  exact-main CI precede any owned provider resource.
- PR #366 merged the retry objective as protected main `2b1597f`; exact-main run `32024585468`
  passed all five jobs before mutation. The unchanged RC29 candidate and success-conditional replay
  both succeeded with distinct run ids, three written model rows, three passing assertions, and zero
  retries. Cleanup began at minute 26.34 and active resource absence was observed at minute 54.52.
  This closes Azure canonical correctness/lifecycle only; scale, provider-posted cost, pairwise,
  soak, final closure, and support remain open under the held USD 2 bound.
- GCP Billing Reports later posted the exact proof project's August 16 provider rows for the
  disposable GKE audit. Compute Engine was USD 0.05 net; Kubernetes Engine and Networking were USD
  0.00 net after credits. The USD 0.05 total passes the USD 0.50 ceiling. The raw reporter record is
  preserved, while a final derivative corrects its unused catalog context to `none`. This closes
  only the hosted GKE bounded-memory cost gate; this scale-matrix ticket remains open.
- PR #368 merged that finalization as protected main `5d0afaa`; exact-main run `32036096345` passed
  all five jobs. The next provider-specific slice binds exact RC29 to Snowflake bulk only: the
  accepted 500,000-row narrow and 200,000-row wide workload, bounded 50,000-row/16-MiB COPY parts,
  exact readback and cleanup, and a USD 0.50 ceiling. Protected merge and exact-main CI precede
  mutation. The full bound leaves USD 3.25 unreserved and remains held until measured usage posts.
- PR #370 merged the passing Snowflake bulk result as protected main `cb8a42c`; exact-main run
  `32044249946` preserved one GitHub action-download failure and passed all five jobs on targeted
  attempt 2. The next protected objective binds exact RC29 to the accepted 300,000-row seed and
  3,000-row incremental delta with exact half-update/half-insert readback, a rejected cursor
  regression, bounded COPY parts, exact cleanup, and a USD 0.50 ceiling. Its full reservation leaves
  USD 2.75 unreserved; protected merge and exact-main CI precede any owned Snowflake object.
- PR #372 merged that incremental objective as protected main `5bc3c6f`; exact-main run
  `32046930482` passed all five jobs before mutation. One exact-RC29 candidate passed exact
  half-update/half-insert readback, cursor monotonicity, COPY telemetry, throughput, and cleanup
  with zero retries. Provider cost remains pending under the held USD 0.50 bound; this ticket stays
  open for the remaining provider classes, posted costs, pairwise, soak, and final closure.
- PR #373 merged the sanitized incremental result as protected main `d7075db`; exact-main run
  `32049861930` passed all five jobs. The next focused implementation extends the protected
  Snowflake scale harness with four independent 5,000-row COPY pipelines plus two controlled claims
  on one target and a stale-publication rejection. It adds no objective approval, provider
  mutation, paid run, candidate change, cost claim, or support promotion.
- PR #374 merged that concurrency harness as protected main `606e19c`; exact-main run `32051585864`
  passed all five jobs. The next focused objective binds the unchanged RC29 candidate and protected
  harness to the four-pipeline/5,000-row workload,
  controlled contention, stale-publication rejection, exact readback, throughput, cleanup, and a
  USD 0.50 ceiling. Its full reservation leaves USD 2.25 unreserved; protected objective merge and
  required exact-main CI precede any owned Snowflake object.
- PR #376 merged the passing concurrency evidence as protected main `4a279cd`; exact-main run
  `32056495930` passed all five jobs. PR #377 then merged the credential-free transform harness as
  protected main `5947d792`; exact-main run `32057603919` passed all five jobs. The next objective
  binds unchanged RC29 to the accepted 100,000-fact/100-dimension transform shape, exact model and
  generic-test results, bounded COPY,
  fencing, cleanup, and a USD 0.50 ceiling. Its full reservation leaves USD 1.75 unreserved;
  protected objective merge and required exact-main CI precede any owned Snowflake object.
- PR #379 merged the passing transform evidence as protected main `d78b356`; exact-main run
  `32063148480` passed all five jobs. PR #380 then merged the credential-free Snowflake failure
  harness as protected main `2e45ca4`; exact-main run `32065584378` passed all five jobs. The next
  objective binds unchanged RC29 to four bounded connector, session, and provider-fence probes plus
  exact cleanup. Its full USD 0.50 reservation leaves USD 1.25 unreserved; protected objective
  merge and required exact-main CI precede any owned Snowflake object.
- PR #387 merged the classified Snowflake failure rerun evidence as protected main `252075f`; all
  four failure probes passed, cleanup completed in 291 seconds, and exact-main run `32393144903`
  passed all five jobs. DANDER-229 is behaviorally closed without changing RC29.
- The 2026-08-20 read-only cost reconciliation used no workload reruns. Azure ActualCost posted
  USD 0.073502213 across the four exact resource groups; their combined Azure and Snowflake costs
  each pass the committed USD 2 ceiling. Snowflake metering and the USD 2.00 effective rate put all
  named Dander warehouse usage at USD 0.6948; bulk, incremental, concurrency, transform, and both
  failure attempts pass their USD 0.50 gates. Temporary Snowflake billing tokens were removed.
- GCP's exact August 16 project/service report still attributes USD 0.05 to the disposable GKE run.
  AWS Cost Explorer marks the current period estimated, but the entire account's gross Usage cost
  from August 14 through 20 is a conservative USD 1.1666027027 upper bound, below the USD 3 AWS
  ceiling even before credits or Phase 8 attribution. Recheck the final AWS invoice without
  rerunning a workload; the concrete scale and launcher cells remain open.
- PR #390 corrected only the BigQuery verification alias after failed harness job
  `d96a56ec-a51b-427f-8521-35eb7e620a4e`; exact-main run `32405100365` passed all five jobs before
  the one authorized corrective execution. Unchanged RC29 then passed the accepted 500,000-row
  narrow and 200,000-row wide BigQuery bulk workload at 2,823.790 and 2,062.642 rows/second,
  respectively, with zero retries, 223,346,688 provider-billed bytes, USD 0.001269578934 gross
  analysis cost, zero reservation or staging records, and exact dataset cleanup. This closes only
  the exact-candidate BigQuery bulk-throughput cell; every other open matrix cell remains open.
- PR #392 merged the exact-RC29 BigQuery incremental harness and USD 0.25 objective as protected
  main `41eace2`; exact-main run `32412152282` passed all five jobs before the only authorized
  execution. The accepted 300,000-row seed and 3,000-row half-update/half-insert delta produced
  exact 301,500-row readback at 334.523 delta rows/second, rejected one cursor regression before
  provider mutation, used zero retries, billed 138,412,032 provider bytes for USD 0.000786781311,
  left zero reservation or staging records, and removed the dataset exactly. This closes only the
  exact-candidate BigQuery incremental cell; every other open matrix cell remains open.
