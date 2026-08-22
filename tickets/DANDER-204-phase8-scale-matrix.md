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
- PR #411 protected one exact-RC30 GKE Standard/PostgreSQL concurrency objective; exact-main run
  `32511415983` passed before its only candidate execution. RC30 failed on a concurrent first-claim
  schema-creation race with zero retries, then removed all owned PostgreSQL and provider resources.
- PR #412 protected the scoped transaction-advisory-lock correction, and PR #413 published private
  RC31 index `sha256:26dac10d6cd8…3c387c9`; both exact-main runs passed all five jobs.
- PR #414 protected the one corrective RC31 objective as main `f092334`; exact-main run
  `32520375521` passed before mutation. The unchanged four-by-5,000 workload then produced exact
  20,000-row readback, one stale-publication rejection, 12,674.271 rows/second, zero retries or
  Warning events, zero staging residue, and exact cluster/network/IAM/API-state cleanup.
- The combined RC30/RC31 provider cost has not posted, so the concurrency functional gate passes
  but this cell remains `not_evaluated` until the exact cost is reconciled without rerunning it.
- PR #416 protected one exact-RC31 GKE Standard/PostgreSQL crossover objective as main `21da4d4`;
  exact-main run `32532555063` passed all five jobs before mutation. Its only candidate execution
  preserved COPY/DIRECT canonical equality across five repetitions of 1, 10, 100, 1,000, and
  5,000 rows, measured 61,110 rows in 7.932 seconds at 7,704.236 rows/second, and recorded an
  environment-specific 10-row/1,490-byte direct threshold without changing a product default.
- The crossover run used zero candidate, Kubernetes Job, or provider-operation retries; emitted no
  Warning events; left zero staging relations or Dander schemas; and removed its namespace,
  cluster, network, IAM grants, credentials, and temporarily enabled APIs exactly. Provider cost
  has not posted, so this cell remains `not_evaluated` without rerunning the workload.
- The next focused objective binds the accepted seven-input/three-output SCD1 correctness fixture
  to exact RC31 on one disposable zonal GKE Standard cluster. It runs correctness only, requires
  the protected harness hash, exact normalized output and replay equality, zero candidate or
  provider-operation retries, exact cleanup, and provider-posted cost under USD 0.50.
- PR #418 protected that objective as main `3b21e36`; exact-main run `32536928917` passed all five
  jobs before mutation. Its only candidate execution produced the exact three-row normalized SCD1
  output, replayed equally, completed COPY, and ran in 70 ms at 42.857 rows/second with zero
  candidate, Kubernetes Job, or provider-operation retries and no Warning events.
- Cleanup began 45 seconds after Job completion and removed every owned PostgreSQL schema, staging
  relation, namespace, cluster, network, IAM grant, local credential, and temporarily enabled API.
  Provider cost has not posted, so correctness functionally passes but remains `not_evaluated`
  without rerunning the workload.
- The next focused objective binds the accepted 500,000-row narrow and 200,000-row wide PostgreSQL
  bulk workload to exact RC31 on one disposable zonal GKE Standard cluster. It runs bulk only,
  requires the protected harness hash, COPY completion and throughput for both shapes, zero
  candidate or provider-operation retries, exact cleanup, and provider-posted cost under USD 0.50.
- PR #420 protected that objective as main `6ac1da7`; exact-main run `32540729024` passed all five
  jobs before mutation. Its only candidate execution completed exact 500,000-row narrow and
  200,000-row wide COPY workloads at 10,356.041 and 8,773.084 rows/second, respectively, with zero
  candidate, Kubernetes Job, or provider-operation retries and no Warning events on the final Job.
- Three PostgreSQL setup Pods and two unscheduled Job objects were corrected before any candidate
  container started. The final Job preserved the 2 CPU limit with a schedulable 1 CPU request and
  ran once; these infrastructure corrections did not retry candidate code or a provider operation.
- Cleanup started 83 seconds after Job completion and removed every owned PostgreSQL schema,
  staging relation, namespace, cluster, network, IAM grant, local credential, and temporarily
  enabled API. Provider cost has not posted, so bulk functionally passes but remains
  `not_evaluated` without rerunning the workload.
- The next focused objective binds the accepted 300,000-row seed and 3,000-row delta PostgreSQL
  incremental workload to exact RC31 on one disposable zonal GKE Standard cluster. It runs
  incremental only, requires the protected harness hash, exact readback, monotonic cursor and
  rejected-regression checks, zero candidate or provider-operation retries, exact cleanup, and
  provider-posted cost under USD 0.50.
- PR #422 protected that objective as main `ef2ab62`; exact-main run `32545149448` passed all five
  jobs before mutation. Its only candidate execution produced exact 301,500-row readback from the
  300,000-row seed and 3,000-row half-update/half-insert delta, preserved cursor monotonicity,
  rejected the cursor regression with zero affected rows, and measured 13,392.857 delta rows per
  second.
- The candidate, Kubernetes Job, and provider cluster operation each ran without retry or restart.
  One readiness-probe Warning preceded PostgreSQL readiness; the candidate emitted no Warning
  event and completed successfully through the required TLS connection.
- Cleanup began 43 seconds after Job completion and removed every owned PostgreSQL schema, staging
  relation, namespace, cluster, network, IAM grant, local credential, and temporarily enabled API.
  Provider cost has not posted, so incremental functionally passes but remains `not_evaluated`
  without rerunning the workload.
- The next focused objective binds the accepted 100,000-fact/100-dimension PostgreSQL transform
  shape to exact RC31 on one disposable zonal GKE Standard cluster. It runs scan, join,
  aggregation, and incremental-merge models with the existing 21 generic assertions, fenced
  publication, transform-duration measurement, zero candidate or provider-operation retries,
  exact cleanup, and provider-posted cost under USD 0.50.
- PR #424 protected that objective as main `94c7816`; exact-main run `32548481891` passed all five
  jobs before mutation. Its only candidate execution produced exact 100,000-row scan and join
  output, ten-category aggregation across 100,000 rows, and a 100,001-row incremental result with
  one update and one insert. All 21 accepted assertions passed through fenced publication.
- The transform completed in 13.845 seconds at 7,222.896 output rows/second with zero candidate,
  Kubernetes Job, or provider cluster operation retries. One readiness-probe Warning preceded
  PostgreSQL readiness; the candidate emitted no Warning event.
- Cleanup began 68 seconds after Job completion and removed every owned PostgreSQL schema, staging
  relation, namespace, cluster, network, IAM grant, active local credential path, and temporarily
  enabled API. Provider cost has not posted, so transform functionally passes but remains
  `not_evaluated` without rerunning the workload.
- The next focused objective binds the four accepted PostgreSQL failure probes to exact RC31 on
  one disposable zonal GKE Standard cluster: bounded pool exhaustion, dropped-connection
  observation, recovered state operations, and cancellation rollback. It runs failure only with
  zero candidate or provider-operation retries, exact cleanup, and provider-posted cost under
  USD 0.50.
- PR #426 protected the failure-only harness and objective as main `cef3fc8`; exact-main run
  `32551541344` passed all five jobs. The first provider Job preserved as UID
  `4c6db228-bde8-403c-a078-a382c29479d7` exited 127 before Dander code because its operator
  manifest overrode the immutable image's `dander` entrypoint with the absent path
  `/app/.venv/bin/dander`. It emitted no report, used zero retries, changed no database state,
  and cleanup removed every owned provider and local resource.
- The corrective objective keeps RC31, the four probes, provider shape, runtime bounds, combined
  USD 0.50 ceiling, and zero-retry policy unchanged. It binds Kubernetes args to the immutable
  `dander` entrypoint, injects the DSN from an owned Secret, and fixes only the PostgreSQL
  readiness executable before the one allowed corrective execution.
- The next AWS-focused objective binds the accepted 500,000-row narrow and 200,000-row wide
  Redshift bulk workload to exact RC31. It uses the existing disposable 8-RPU Serverless data
  plane and one transient Fargate harness task, requires COPY-only completion and exact readback,
  derives provider cost from `SYS_SERVERLESS_USAGE.charged_seconds`, disables every automatic and
  provider-operation retry, and requires exact schema, staging, harness, and data-plane cleanup
  under a USD 0.50 cell ceiling inside the existing USD 3 AWS allocation.
- The initial Redshift bulk setup started no candidate task: ECS rejected the objective's invalid
  2-vCPU/2-GiB Fargate pair during task-definition registration. It used zero retries and cleanup
  removed the harness object, transient roles and logs, data plane, and every remote-state version;
  the exact RC31 ECR index remains retained. The corrective objective changes only task memory to
  the smallest compatible 4 GiB, binds that shape in the protected harness, and reserves the one
  authorized corrective candidate execution under the unchanged combined USD 0.50 ceiling.
- PR #429 protected the corrected Fargate shape as main `4d6151b`; exact-main run `32568141092`
  passed all five jobs. Its one RC31 candidate execution, `rc31-redshift-bulk-corrective-20260822`,
  reached Redshift connection validation and timed out after the configured 300 seconds before
  schema creation or workload rows. The transient task role omitted the globally scoped
  `tag:GetResources` and `tag:GetTagKeys` reads required to resolve its `RedshiftDbRoles` tag, so
  the result is a blocked operator-IAM qualification attempt, not a product result. It used zero
  retries, retained the full accepted workload unexecuted, and exact cleanup removed the task,
  schema/staging scope, data plane, roles, logs, state machine, and remote state. No further bulk
  execution is authorized.
- The next focused objective binds the accepted 300,000-row seed and 3,000-row half-update/
  half-insert incremental workload to exact RC31 on one disposable 8-RPU Redshift Serverless data
  plane. It requires exact 301,500-row readback, monotonic cursor advancement, one regression
  rejected before provider mutation, COPY-only throughput, provider-measured cost, zero retries,
  and exact schema, staging, harness, and data-plane cleanup under USD 0.50. Its fail-closed
  approval also binds the complete Serverless task-role tag and global tag-read requirements.
- The initial incremental task exited 127 before Dander because its operator launcher addressed
  absent `/app/.venv/bin` executables and requested X86_64 for the retained ARM64 RC31 image. It
  emitted no report, performed no workload mutation, and used zero retries. Exact cleanup removed
  all transient harness resources, all 37 data-plane resources, and every remote-state version.
  The corrective objective keeps the candidate, workload, provider shape, and combined USD 0.50
  ceiling unchanged, binds ARM64 plus the image's PATH-resolved `python` and `dander` executables,
  and authorizes only the one remaining corrective execution.
- The next focused objective binds four independent 5,000-row Redshift pipelines and two
  controlled claims to exact RC31 on one disposable 8-RPU Serverless data plane. It requires exact
  20,000-row readback, stale-publication rejection, throughput, provider-measured cost, zero
  candidate or provider-operation retries, and exact schema, staging, harness, and data-plane
  cleanup under the existing USD 0.50 cell ceiling. The protected launcher is ARM64 and retains
  terminal output only in the owned staging prefix until immediate cleanup.
- The initial protected concurrency execution
  `rc31-redshift-concurrency-20260822` ran the exact ARM64 RC31 digest once with zero retries and
  exited 2 before producing a report. Its sanitized diagnostic reported a pre-report failure and
  successful run-scoped cleanup; external cleanup then removed all transient launcher resources,
  all 37 data-plane resources, and every remote-state version while retaining only the immutable
  candidate. The corrective objective keeps the candidate, workload, provider shape, and cleanup
  contract unchanged and initializes the shared fence table before independent pipeline claims
  enter worker threads, removing concurrent first-use DDL from the one remaining execution.
- PR #433 protected the corrective concurrency harness as main `1015a2d`; exact-main run
  `32577343621` passed all five jobs. Its only corrective execution
  `rc31-redshift-concurrency-corrective-20260822` ran the same exact RC31 digest with zero retries
  and exited 2 after the 300-second Redshift connection-validation bound, producing no report.
  Immediate cleanup removed the disposable schema, staging and diagnostics, every transient
  launcher resource, all 37 data-plane resources, and every remote-state version while retaining
  only the immutable candidate. No further concurrency execution is authorized.
- The next focused objective binds the accepted 100,000-fact/100-dimension transform workload to
  exact RC31 on one disposable 8-RPU Redshift Serverless data plane. It runs scan, join,
  aggregation, and incremental-merge models with the existing 21 generic assertions, requires
  exact readback and fenced publication, derives provider cost from `SYS_SERVERLESS_USAGE`, uses
  zero candidate or provider-operation retries, and requires exact source/target schema, staging,
  harness, and data-plane cleanup under USD 0.50.
