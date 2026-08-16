---
id: DANDER-202
title: Implement the AWS-native canonical profile
status: in-code
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200]
created: 2026-08-13
---

## Context

Fargate lifecycle acceptance currently covers only the GCP data plane. The factory and Terraform
bootstrap reject a non-GCP profile, so AWS-native qualification cannot begin yet.

## Acceptance Criteria

- [x] One typed Fargate projection supports Redshift, PostgreSQL state, Glue, and AWS Secrets
  Manager without weakening the accepted GCP projection.
- [x] Terraform and operations remain manifest-bound, plan-first, least-privileged, and keyless.
- [x] Contract tests cover projection, secret binding, provider assembly, and fail-closed invalid
  compositions.
- [x] Protected CI and independent completion review pass for the runtime-overlay correction before
  a replacement qualification candidate is cut.

## Implementation Notes

- The selected version-2 manifest retains typed AWS Secrets Manager coordinates and permits only
  the named Redshift/PostgreSQL/Glue/AWS-Secrets composition or the accepted GCP composition.
- AWS-native secrets are full account-and-region-bound ARNs. The task role receives only declared
  secret reads, Redshift authentication, the configured wildcard-free staging prefix, and the
  configured Glue namespace. Terraform passes only a non-secret binding document; the runtime
  resolves values with the task role and removes them after execution.
- Independent review caught the unresolved-DSN, IAM-wildcard, and COPY-role partition defects
  before live use; focused runtime and Terraform regression contracts cover the corrections.
- Fargate retains the exact selected deployment separately from the shared platform profile, so
  runtime configuration remains unambiguous when staging and production reuse one profile.
- Local Python, Terraform validation, provider-mocked Terraform tests, independent review, PR
  checks, and exact-main CI pass at `fe325ff`. Live AWS qualification and support promotion remain
  separate open gates; RC21 is the first candidate cut after this implementation merged.
- Exact RC22 live preflight later exposed a packaging defect before the Fargate plan: its immutable
  image contains only the GCP and Kubernetes deployment coordinates, so the AWS-native command
  cannot resolve its selected deployment. The disposable data plane was removed without a task run.
- The local correction projects the validated, selected non-secret platform overlay through the
  task definition and materializes it only in runtime scratch space. The first completion review
  then caught missing self-scoped database egress, direct lookahead inside an open transaction, and
  an understated crossover threshold. Commit `8240bcfc3585b8217a607cb08d2d97290ca13afa`
  corrects all three with focused Python and Terraform checks and protected CI run `31863498217`.
- The exact-head rereview then caught quadratic Fargate overlay projection and a non-monotonic
  crossover recommendation. Commit `b7a3181325a92091ea7cc50046e1912fc637ca92` scopes each runtime
  overlay to its task, moves the aggregate projection out of the process argument, and permits only
  a contiguous DIRECT-winning prefix. Protected run `31864784027` passed all five jobs.
- The next rereview caught that the disposable task had no NAT, private service endpoints, or public
  IP. Commit `b6b479d04df338e5c9747caf55922cfd1edc7516` binds this fixture to public-IP
  assignment while preserving zero inbound access, TLS-only public egress, and self-scoped database
  traffic. Protected run `31865699608` passed all five jobs.
- The following review caught that a root-level Terraform check only warned on a wrong authenticated
  account. Commit `cfe8e634a43c26868eb1f622c8d59ea3688ad7a7` constrains the AWS provider to the
  authorized account and adds a blocking lifecycle precondition with a negative plan test.
  Protected run `31866450352` passed all five jobs.
- The next exact-head review caught that the new Serverless namespace did not grant its IAM-derived
  task user database DDL/COPY rights and an oversized overlay could escape the CLI error boundary.
  Commit `553a15a8f678ba9860ce6284c0d6089acbbeb9e2` provisions and maps one explicit
  database role and translates the overlay rejection. Protected run `31867794981` passed all five
  jobs. The next review caught that the baked version-one project ignored the projected deployment;
  commit `055e3a2` resolves its logical intent through an explicit external overlay. Protected CI and
  rereview passed at head `34d6d55` in run `31868849725`, but that review found the historical
  Greenhouse fixture could not pass Redshift schema preflight and runtime-created Glue assets had no
  cleanup owner. Commit `533125a92af722ed391760923fff4d926ead80f6` replaces the qualification
  workload with an immutable flat scalar fixture, makes the disposable Terraform root own the exact
  Glue database/table, and restores both Phase 8 PostgreSQL harnesses to the source distribution.
  Protected run `31870117994` passed all five jobs and exact-head rereview accepted those fixes. It
  then caught unsupported view materialization, stale RC22 Terraform identity, and a provisioned
  Redshift role-validation gap. Commit `9c6e27b04a9477c3039e7d6e085111f045021fc0` uses table
  materialization, requires exact candidate input for tags and staging, and rejects the
  Serverless-only field on provisioned clusters. Protected head `0b1a8fa` passed all five jobs in
  run `31871007170`; ninth exact-head review accepted those fixes and found four qualification-root
  blockers. Commit `b0314031297977192935a54f98921922b6e2ad26` adds Redshift Serverless COPY trust,
  attaches two action-bounded qualification policies to the short-lived deployment role without
  changing D7, and rejects RDS-invalid names plus unusable VPC parent ranges. Full local tests,
  Access Analyzer validation, package validation, Terraform validation/mock tests, lint, and typing
  passed; protected head `4c82438` then passed all five jobs in run `31873024315`. Tenth exact-head
  review accepted those four fixes and found missing Redshift create dependencies, Glue tag
  lifecycle permissions, and whole-number usage-limit validation. Commit `7a1f429` corrects those
  three; head `d644b2a` passed all five protected jobs in run `31874238906`. Eleventh exact-head
  review accepted those corrections and found missing Serverless Data API credentials plus residual
  staging-object version cleanup authority. Commit `ef18330` corrected both; head `67ab738` passed
  protected run `31875414186`, but twelfth review found forced cleanup also required exact version
  deletion. Commit `06ec187` added that scoped action. Current-main integration head `3ea34e2`
  passed all five protected jobs in run `31876449299`, and focused thirteenth review accepted the
  complete delta. Reconciliation head `0c65e42` passed run `31877158743`; fourteenth review found
  security-group creation and EC2 tag-ownership blockers. Commit `b9735c9` corrects both;
  correction/current-main head `d8a18ec` passed run `31878215886`, and focused fifteenth review
  accepted the correction. Docs-closure head `6ede9da` passed run `31879161660`; sixteenth review
  found that route-table, subnet, and VPC-endpoint creation still lacked their tagged VPC/route-table
  dependency dimensions. Commit `e12ee59` corrects them; correction/docs head `0da600b` passed run
  `31879898267`, and focused seventeenth review accepted the correction. PR #291 merged the baseline
  as protected-main commit `3d7783c`; exact-main run `31882061192` passed. PR #298 merged private
  RC24 at protected main `c19de39`, exact-main run `31882919709` passed, and source-free
  multi-platform candidate `sha256:b7eadc7e…9488` passed external AWS-overlay selection without
  provider access. The separate exact-objective live-profile lane and support acceptance remain open.
- The first complete RC24 AWS-native Fargate launch later failed before provider construction
  because the shared launcher identity hook required Google federation for every Fargate task.
  AWS-native Fargate now keeps its ECS task role ambient when no Google federation is declared;
  Fargate-to-GCP behavior is unchanged and partial federation configuration still fails closed.
- That execution also exposed a separate operator-read boundary: stage zero's exact-name log ARN did
  not cover the hyphen-suffixed RC24 deployment name. The scoped correction retains the exact-name
  ARN and adds only `${name}-*` task-log groups in the same account, region, and `/dander/` namespace.
- PR #317 merged private RC25 at protected main `f5935a6`; exact-main run `31902553474` passed all
  five jobs. Source-free multi-platform index `sha256:5a0d5520…2238` passed both-architecture,
  external AWS-overlay, and read-only checks. The replacement-candidate gate is complete; a fresh
  exact-objective live qualification lane and support acceptance remain open.
- PR #318 merged the sanitized RC25 publication record as protected-main `ae3be54`; exact-main run
  `31903775539` passed all five jobs. The fresh AWS correctness gate binds one manual execution and
  one replay to exact RC25, `us-east-1`, paused scheduling, exact cleanup, and the recorded USD 3
  allocation. Live mutation waits for this objective commit's protected review and exact-main CI.
- The first RC25 platform apply was interrupted by a transient CloudWatch Logs DNS failure. Its
  reviewed 13-create reconciliation then exposed that the stage-zero EventBridge tag-read scope
  omitted the exact stable `dander-controller-failures` rule while retaining only the named
  deployment pattern. No task ran. The 21-resource partial platform and 36-resource data plane were
  removed, both states and direct inventories are empty, and the platform KMS key is pending
  deletion. Qualification resumes only after the exact-rule read correction reaches protected main
  and a reviewed stage-zero policy update is drift-free.
- PR #320 merged the exact stable-rule read as protected main `7155d54`; the reviewed stage-zero
  update applied `0/1/0`, was drift-free, and retained bounded IAM simulation. The next fresh RC25
  task resolved its AWS secret and obtained Redshift credentials, but the 30-second connector limit
  expired while Serverless cold-started network interfaces. No provider operation or replay ran.
  Exact saved-plan cleanup removed all 25 platform and 36 data-plane resources. RC25 remains valid;
  PR #321 merged the sanitized failure record as protected main `b784318`. The focused replacement
  objective binds a 120-second Redshift connection timeout under the unchanged 600-second runtime
  deadline without changing RC25 or the global provider default. It must pass protected merge and
  exact-main CI before the complete objective reruns.
- PR #324 preserved the corrected-timeout retry as protected main `804496e`; exact-main run
  `31914082961` passed all five jobs. The task connected and created its temporary table, then COPY
  failed because the runtime database role had ASSUMEROLE on `default` while the writer supplied the
  explicit staging-role ARN. The focused correction grants only that exact role ARN to
  `dander_runtime` for COPY; a replacement candidate and complete live objective remain separate.
- PR #325 merged that focused grant as protected main `7cea5a8`, and PR #326 merged private RC26 as
  protected main `f0fe54f`; both exact-main runs passed all five jobs. Source-free multi-platform
  index `sha256:e63aef4b…d28e` passed both-architecture, external AWS-overlay, attestation, and
  rootless read-only checks. PR #327 merged the publication record as protected main `6e9d65e`, and
  exact-main run `31916736418` passed all five jobs. The fresh RC26-bound objective preserves one
  manual run, one replay, the reviewed timeout, exact cleanup, and the USD 3 allocation. Its manual
  task completed PostgreSQL setup but expired during Redshift connection validation after 121,066
  ms; no provider operation or replay ran. Reviewed saved-plan cleanup removed all 25 platform and
  36 data-plane resources, both states and direct active inventories are empty, and RC26 remains
  current. The consumed objective transfers no result; another AWS run requires a fresh protected
  objective that isolates the connection delay.
- PR #330 merged the sanitized attempt as protected main `730de0b`; exact-main run `31920702822`
  passed all five jobs. The replacement objective preserves exact RC26, one manual run, one
  success-conditional replay, paused scheduling, exact cleanup, and the existing cumulative USD 3
  allocation while binding a 300-second Redshift connection window below the unchanged 600-second
  runtime deadline. It must reach protected main before another AWS mutation.
- The replacement objective reached protected main as `890853d`; exact-main run `31921459727`
  passed all five jobs. Its manual task authenticated to the private Redshift endpoint as the exact
  task role and set `application_name=dander`, but no runtime-user query entered query history
  before the Python driver hit the 300-second startup timeout. Replay did not start. Exact cleanup
  removed all 25 platform and 36 data-plane resources, both states and direct inventories are
  empty, and the attempt KMS key is pending deletion. RC26 is not qualified; a focused
  connection-startup correction, replacement candidate, and fresh objective are required.
- PR #333 merged the focused Redshift Serverless base-protocol correction as protected main
  `141fab6`; exact-main run `31924339366` passed all five jobs. PR #334 merged private RC27 as
  protected main `d7ac61f`; exact-main run `31925228450` passed all five jobs. Source-free
  multi-platform index `sha256:bcf62d2c…4e09c` passed both-architecture, external AWS-overlay,
  attestation, and rootless read-only checks under the unchanged aggregate authorization. Its
  publication record merged in PR #335 as protected main `ea3e260`; exact-main run `31926577710`
  passed all five jobs. The fresh RC27-bound objective preserves one manual run, one
  success-conditional replay, the reviewed 300-second timeout, paused scheduling, exact cleanup,
  and the cumulative USD 3 allocation. Protected review and the complete live rerun remain open;
  RC26 results do not transfer.
