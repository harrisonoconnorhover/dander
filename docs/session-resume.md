# Session Resume — 2026-08-16

Read `HANDOFF.md`, `docs/decisions.md`, `docs/spec-alignment.md`, and
`docs/release-audit.md` before changing code or cloud resources.

## Public releases

- Dander `0.9.0rc20` is the current public beta.
- Salesforce `0.3.1` and ServiceNow `0.2.2` are the current stable connector releases for Dander
  `0.7.x`; their accepted release candidates remain recorded in the Phase 1 evidence.
- Druff's fork contains Josh's reconciled graph-client ancestry and the later persistence,
  execution, catalog, operation-authoring, and deployment-preview work.

## Retained project

- Project `dander-proof-harrison-20260801`, region `us-central1`, remote states `dander/state` and
  `dander/bootstrap-admin/state` in `dander-proof-harrison-20260801-dander-state`.
- The five retained jobs use private qualification candidate Dander `0.9.0rc22` index
  `sha256:ce395dda3865691d2300f57577fb9b5297031293f77c89f6adc34f60853947c3`.
  Private RC27 at `sha256:bcf62d2c…4e09c` is the latest published candidate but has not replaced
  those retained jobs. Its AWS-native correctness and named local Kubernetes five-class slices
  passed, but provider cost and the remaining Phase 8 gates are open, so it is not fully qualified.
  Public RC20 remains unchanged.
- Greenhouse, HubSpot, Salesforce, and ServiceNow are enabled daily at 09:00, 10:00, 11:00, and
  12:00 America/New_York. The executable Greenhouse graph remains paused at 13:00.
- The simulation-only managed cost guard, alerts, secrets, datasets, cursors, leases, and retained
  proof data remain in place.

## Druff

- The source-free Druff image is pinned to
  `sha256:a5e255d6adcdc920f65fa485f14480d0667db0aa8c179e30507425638bb3871c`.
- The public static interface is <https://dander-druff-yos2b3gbca-uc.a.run.app>. Its dedicated
  service account has no project roles and its Cloud Run service is limited to one instance.
- Interactive open/save, validation, execution, status, and deployment preview still require an
  operator-started `dander graph serve` loopback service with the exact hosted origin allowed.

## Latest operating evidence

- Exact private RC27 passed the five protected Kubernetes objectives from main `6ff041f` after
  exact-main CI run `31942160724` passed all five jobs. One named kind 1.32.2 arm64 cluster ran
  correctness, bulk, incremental, transform, and PostgreSQL-specific failure against TLS
  PostgreSQL 15.18 with PostgreSQL state/warehouse, catalog `none`, an existing Secret projection,
  2 CPU/512 MiB, a 600-second deadline, zero retries, and reporter-sidecar collection. All reports
  bind exact candidate/objective identity and non-estimated USD 0 cost. Exact cleanup left zero
  Dander schemas, staging relations, Warning events, clusters, node containers, or temporary image
  tags. This closes the named local profile and five-class launcher-scale slice only; hosted
  Kubernetes scale/cost, remaining launcher classes, soak, and support stay open. See
  `docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-scale-attempts.json`.
  PR #339 merged that sanitized result as protected main `b73fafc`; exact-main run `31943674409`
  passed all five jobs. PR #340 merged the exact-RC27 Kubernetes bounded-memory objective as
  protected main `72a422e`; exact-main run `31944524241` passed all five jobs before execution. The
  disposable 2 CPU/256 MiB Job then processed 2.7248 GB logical input in 129.180 seconds at 20,127
  rows/second with 176,115,712 bytes peak RSS. TLS, reporter collection, zero retries/restarts, zero
  Warning events, database cleanup, USD 0 cost, cluster cleanup, and temporary-tag cleanup passed.
  One harness-only runtime-path preflight was corrected against the immutable image before the
  cluster was recreated; no RC22 result transferred. PR #341 merged the sanitized bounded-memory
  evidence as
  protected main `f864a2b`; exact-main run `31945860151` passed all five jobs, after which its local
  TLS/operator package was deleted. The committed concurrency objective reuses the exact protected
  2.6-million-row/256 MiB configuration but approves only four independent 5,000-row pipelines,
  stale-fence rejection, throughput, cleanup, and USD 0 cost. PR #342 merged it as protected main
  `7dc51f8`; exact-main run `31946605370` passed all five jobs before execution. Exact RC27 then
  completed 20,000 concurrent rows in 334.55 ms at 59,781.789 rows/second, rejected the stale
  publication fence, and left no database residue. TLS, reporter collection, zero retries/restarts,
  zero Warning events, USD 0 cost, cluster cleanup, and temporary-tag cleanup passed. One
  harness-only PostgreSQL storage preflight stopped before the candidate Job existed; the corrected
  run used a freshly recreated cluster. PR #343 merged the sanitized concurrency evidence as
  protected main `bd7489d`; exact-main run `31948875002` passed all five jobs, after which its
  operator TLS package was moved to Trash. The fresh crossover objective reuses RC24's corrected
  1/10/100/1,000/5,000-row, 128-byte, five-repetition COPY/DIRECT workload while binding exact RC27,
  `kubernetes_portable`, canonical equality, measured threshold, cleanup, and USD 0 cost. Protected
  merge and exact-main CI precede execution; neither RC24's result nor its zero threshold transfers.
  PR #344 merged that objective as protected main `4166afb`; exact-main run `31949803615` passed all
  five jobs before execution. One disposable kind 1.32.2 arm64 Job then passed all seven objectives,
  processing 61,110 rows in 2.433 seconds at 25,117.139 rows/second with 177,549,312 bytes peak RSS.
  COPY and DIRECT were canonically equal; DIRECT tied through 10 rows and lost at larger sizes, so
  this environment-specific measurement recommends 10 rows / 1,490 logical bytes without changing
  a product default. TLS, zero retries/restarts/Warning events, database cleanup, USD 0 local cost,
  cluster cleanup, and temporary-tag cleanup all passed. PR #345 merged the sanitized evidence as
  protected main `366ce8a`; exact-main run `31951009601` passed all five jobs. PR #346 merged the
  GKE bounded-memory objective as protected main `b01bf8b`; exact-main run `31952323045` passed all
  five jobs. Execution used later main `1256213` after exact-main run `31953203115` also passed all
  five jobs, with the benchmark helper unchanged from exact RC27. One disposable zonal GKE Standard
  1.35.6 cluster ran the single candidate attempt on one on-demand `e2-standard-4` amd64 node
  against rootless TLS PostgreSQL 15.18. Exact RC27 processed 2.7248 GB in 356.685 seconds at
  7,289.345 rows/second with 179,863,552 bytes peak RSS below the 80% ceiling. Both candidate and
  reporter exited zero with no retry or restart; no Dander schema or staging relation remained. A
  first infrastructure-only Job hit RC27's immutable `/usr/local/bin/python` path difference before
  candidate code started and was corrected within the two-attempt ceiling. Cleanup removed every
  owned cluster, compute, network, secret, TLS, service-account, and IAM resource and restored
  Compute Engine and GKE APIs to their disabled prestate. Provider billing has not posted, so cost
  and the normalized report remain `not_evaluated`. The raw report also preserves an unused
  `catalog=postgresql` context that must be corrected explicitly only in a later derived final
  report. See
  `docs/evidence/phase8/2026-08-16/gke-standard-rc27-postgresql-bounded-memory-attempts.json`.

- Exact private RC27 passed the protected AWS-native correctness objective from main `c348122`
  after exact-main CI run `31927276568` passed all five jobs. One manual run and one replay both
  exited zero on the exact ECR digest with no provider retry; Redshift retained three distinct
  canonical rows, replay affected zero source rows, all assertions passed twice, Glue published the
  exact metadata, and the staging prefix was empty. Reviewed saved-plan cleanup destroyed all 25
  platform and 36 data-plane resources, both Terraform states and active owned inventories are
  empty, the platform KMS key is pending deletion, and the exact private ECR digest is retained.
  Cost Explorer was denied to the operator role and invoice data is pending, so cost remains
  `not_evaluated` and support was not promoted. See
  `docs/evidence/phase8/2026-08-16/aws-native-rc27-profile.json`. PR #337 merged that sanitized
  evidence as protected main `df018e6`; exact-main CI run `31941210969` passed all five jobs.

- Private Dander `0.9.0rc27` was built from protected-main commit
  `d7ac61f46b362f4e7e64365e9267ec6e7faf70f2` after all five jobs passed exact-main CI run
  `31925228450`. Its exact wheel produced source-free GAR index `sha256:bcf62d2c…4e09c` with
  amd64/arm64 manifests, SPDX SBOM, and SLSA provenance. Both architectures reported RC27; GCP,
  Kubernetes, and externally projected AWS selectors plus rootless read-only conformance on both
  architectures passed. No RC26 result transfers, and publication is not a live-profile, cost,
  public-release, or support pass. PR #335 merged the sanitized record as protected main `ea3e260`;
  exact-main run `31926577710` passed all five jobs. See
  `docs/evidence/phase8/2026-08-16/rc27-candidate.json`.

- Private Dander `0.9.0rc26` was built from protected-main commit
  `f0fe54f797bbbe1cc5110f9b36c4e3e6da48f496` after all five jobs passed exact-main CI run
  `31915564765`. Its exact wheel produced source-free GAR index `sha256:e63aef4b…d28e` with
  amd64/arm64 manifests, SPDX SBOM, and SLSA provenance. Both architectures reported RC26; GCP,
  Kubernetes, and externally projected AWS selectors plus rootless read-only conformance on both
  architectures passed. No RC25 result transfers, and publication is not a live-profile, cost,
  public-release, or support pass. PR #327 merged the sanitized record as protected main `6e9d65e`;
  exact-main run `31916736418` passed all five jobs. See
  `docs/evidence/phase8/2026-08-15/rc26-candidate.json`.

- Private Dander `0.9.0rc25` was built from protected-main commit
  `f5935a6d263cf6734ee9944f2f0e02d025edc63e` after all five jobs passed exact-main CI run
  `31902553474`. Its exact wheel produced source-free GAR index `sha256:5a0d5520…2238` with
  amd64/arm64 manifests, SPDX SBOM, and SLSA provenance. Both architectures reported RC25;
  GCP, Kubernetes, and externally projected AWS selectors plus rootless read-only conformance
  passed. No RC24 report transfers, and publication is not a live-profile, cost, public-release, or
  support pass. PR #318 merged the sanitized evidence as protected-main `ae3be54`; exact-main run
  `31903775539` passed all five jobs. See `docs/evidence/phase8/2026-08-15/rc25-candidate.json`.

- Private Dander `0.9.0rc24` was built from protected-main commit
  `c19de3980411f20514326db9f722f07e57a3d1ef` after all five jobs passed exact-main CI run
  `31882919709`. The exact wheel produced source-free GAR index `sha256:b7eadc7e…9488` with
  amd64/arm64 manifests, SBOM, and provenance. Both architectures reported RC24; GCP,
  Kubernetes, and externally projected AWS selectors plus read-only local conformance passed.
  PR #299 merged the sanitized candidate evidence as protected-main commit `a66ce65`; exact-main
  CI run `31884123337` passed all five jobs. No provider profile or retained workload changed,
  provider cost remains pending, and publication is not a scale, cost, live-profile, or support
  pass. See
  `docs/evidence/phase8/2026-08-15/rc24-candidate.json`.

- Exact private RC24 passed the committed corrected local PostgreSQL crossover objective against
  disposable TLS PostgreSQL 15.18. COPY and DIRECT produced equal canonical rows at every sampled
  size and emitted both selected transports. DIRECT lost at the first sampled size, so no
  contiguous DIRECT-winning prefix exists and the measured recommendation remains disabled at
  zero rows/bytes. All seven objectives passed in 2,650 ms with 177,127,424 bytes peak RSS, zero
  staging relations, exact resource cleanup, and USD 0 measured local cost. This closes only the
  corrected crossover class and still requires protected review. See
  `docs/evidence/phase8/2026-08-15/postgresql-crossover-attempts.json`.

- Azure and OCI interactive authentication were restored and verified through provider APIs on
  2026-08-14. Azure contains no Dander-named resource or resource group. OCI retains its accepted
  Phase 7 foundation and private image history but has zero active Container Instances. The OCI CLI
  omitted the session profile's user field; it was restored from the signed token subject without
  recording an identifier or credential. No cloud mutation occurred. Credentials and replacement
  candidate publication no longer block these providers; each live lane still requires its exact
  committed objective manifest.
  See the Azure and OCI credential-restoration records under
  `docs/evidence/phase8/2026-08-14/`.

- AWS access was restored on 2026-08-14. Exact RC22 was copied byte-identically to private ECR,
  and a pre-approved 28-resource Redshift Serverless/PostgreSQL/Glue/Secrets data plane was created
  under a USD 3 allocation. Read-only image inspection then found no packaged AWS deployment, so
  no Fargate plan, task, or pipeline ran. The exact 28-resource destroy completed, qualification
  state and inventories are empty, and AWS D7 was unchanged. Provider cost remains pending. A local
  correction projects the selected non-secret platform overlay at launch. Successive corrections
  reached qualification-baseline head `3ea34e2`, which passed all five protected jobs in run
  `31876449299`; focused thirteenth review accepted the final version-cleanup permission and
  current-main integration. Reconciliation head `0c65e42` passed run `31877158743`; fourteenth
  review found two EC2 authorization blockers corrected in `b9735c9`. Correction/current-main head
  `d8a18ec` passed run `31878215886`, and focused fifteenth review accepted the correction.
  Docs-closure head `6ede9da` then passed run `31879161660`, but sixteenth review found missing
  existing-resource dimensions for route-table, subnet, and VPC-endpoint creation. Commit `e12ee59`
  adds only qualification-tagged VPC/route-table dependency grants. Correction/docs head `0da600b`
  passed run `31879898267`, and focused seventeenth review accepted the correction. PR #291 merged
  the baseline as protected-main commit `3d7783c`, whose exact CI run `31882061192` passed all five
  jobs. PR #317 merged private RC25 at protected main `f5935a6`; exact-main run `31902553474`
  passed and its source-free replacement index is privately published and locally inspected. AWS
  live qualification was first bound to `docs/evidence/phase8/2026-08-15/aws-native-rc25-profile-objectives.json`:
  one manual run, one replay, paused scheduling, exact cleanup, and a USD 3 ceiling. PR #319 later
  merged the gate as `c79b3d8`; exact-main run `31904727106` passed all five jobs. RC25 platform
  reconciliation then failed before execution because the stage-zero EventBridge tag read omitted
  the exact stable rule name. Its resources were removed, and PR #320 merged the bounded correction
  as protected main `7155d54`; the reviewed stage-zero update applied `0/1/0`, was drift-free, and
  retained exact allow/deny simulation. A fresh RC25 manual task resolved its AWS secret and
  obtained Redshift credentials, then hit the configured 30-second connection limit while
  Serverless cold-started network interfaces. No provider operation or replay ran. Exact saved-plan
  cleanup removed all 25 platform and 36 data-plane resources; both states and direct inventories
  are empty, and the platform KMS key is pending deletion on 2026-09-14. PR #321 merged that
  sanitized failure record as protected main `b784318`. RC25 remains valid. The replacement gate at
  `docs/evidence/phase8/2026-08-15/aws-native-rc25-profile-objectives-v2.json` preserves the exact
  candidate, objectives, and USD 3 ceiling while binding a 120-second Redshift connection timeout
  under the unchanged 600-second runtime deadline. PR #322 merged that gate as protected main
  `ea625e3`; exact-main run `31911384116` passed all five jobs. PR #323 merged the corrected runbook
  as protected main `c14c6fa`; exact-main run `31912057557` passed all five jobs. The replacement
  manual task connected to Redshift and created its temporary table, then COPY failed because the
  runtime database role lacked effective ASSUMEROLE permission on its explicit staging role. Replay
  did not start. Exact saved-plan cleanup again removed all 25 platform and 36 data-plane resources;
  both states and direct inventories are empty, and the attempt KMS key is pending deletion on
  2026-09-14. Provider invoice data is pending. This live-discovered candidate defect required a
  focused implementation PR, replacement candidate, and complete objective rerun. PR #324 merged
  the failure record as protected main `804496e`, and PR #325 merged the exact staging-role grant
  as protected main `7cea5a8`; exact-main CI run `31914830354` passed all five jobs. PR #326 merged
  private RC26 as protected main `f0fe54f`; exact-main run `31915564765` passed all five jobs, and
  its source-free multi-platform index passed local inspection. The fresh gate at
  `docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives.json` preserves one manual
  run, one replay, the 120-second connection timeout, exact cleanup, and the existing USD 3
  allocation. That protected objective was consumed by one exact RC26 manual task. PostgreSQL setup
  completed, then Redshift connection validation expired after 121,066 ms with no provider
  operation or row; replay did not start. The reviewed network coordinates matched and an immediate
  post-failure Data API read completed, but the exact connection delay is not yet proven. Exact
  cleanup removed all 25 platform and 36 data-plane resources; both Terraform states and direct
  active inventories are empty, and the attempt KMS key is pending deletion on 2026-09-14. RC26
  remains current, but the consumed objective transfers no result and must not be reused. PR #330
  merged the sanitized attempt as protected main `730de0b`; exact-main run `31920702822` passed all
  five jobs. The replacement objective at
  `docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives-v2.json` preserves exact RC26,
  one manual run, one success-conditional replay, paused scheduling, exact cleanup, and the existing
  cumulative USD 3 allocation while binding a 300-second connection window below the unchanged
  600-second runtime deadline. It reached protected main as `890853d`; exact-main run `31921459727`
  passed all five jobs. The one manual task reached the private Redshift endpoint, authenticated as
  the exact Fargate task role, and set `application_name=dander`, but no runtime-user query entered
  query history before the Python driver hit the 300-second startup timeout. Replay did not start.
  Exact saved-plan cleanup removed all 25 platform and 36 data-plane resources; both states and
  direct active inventories are empty, and the attempt KMS key is pending deletion on 2026-09-14.
  RC26 is not qualified; a focused connection-startup correction, replacement candidate, and fresh
  protected objective must precede another AWS run. Every
  remaining benchmark/provider objective and any
  live-discovered defect uses a fresh protected-main branch; rerun only materially affected evidence
  plus the eventual final closure matrix. See
  `docs/evidence/phase8/2026-08-15/aws-native-rc26-redshift-driver-startup-attempt.json`.

- PR #332 merged that startup record as protected main `1fa3452`; exact-main run `31923526315`
  passed all five jobs. PR #333 merged the focused Serverless base-protocol correction as protected
  main `141fab6`; exact-main run `31924339366` passed all five jobs. PR #334 merged private RC27 as
  protected main `d7ac61f`; exact-main run `31925228450` passed all five jobs, and its source-free
  multi-platform index passed artifact, selector, attestation, and rootless read-only inspection.
  PR #335 merged the sanitized publication record as protected main `ea3e260`; exact-main run
  `31926577710` passed all five jobs. The fresh gate at
  `docs/evidence/phase8/2026-08-16/aws-native-rc27-profile-objectives.json` preserves one manual run,
  one success-conditional replay, the 300-second connection timeout, paused scheduling, exact
  cleanup, and the cumulative USD 3 allocation. PR #336 merged it as protected main `c348122` and
  exact-main run `31927276568` passed all five jobs. The resulting correctness run is recorded above;
  no RC26 result transferred.

- Private arm64 Dander `0.9.0rc23` at commit `2455fc34d4503863060b7bac873be36319c13e4f`
  was published only to the private qualification registry at index `sha256:8bd35188…3064`. It
  passed exact artifact/runtime/security preflight and the pre-approved local DIRECT-to-COPY
  crossover against TLS PostgreSQL 15.18. DIRECT tied COPY only at 10 rows, but completion review
  found its 1,400-byte threshold omitted field-name bytes counted by the writer and would select
  COPY. The corrected harness derives 1,490 bytes and buffers bounded lookahead before opening a
  transaction. RC23's threshold objective remains invalid and is superseded by RC24's corrected
  zero-threshold result above.
  See `docs/evidence/phase8/2026-08-14/phase8-completion-review.json`.

- Exact RC22 passed five normalized Kubernetes launcher classes on kind 1.32.2 under a 2 CPU/512
  MiB limit, 600-second deadline, and zero retries against TLS PostgreSQL 15.18. Correctness, bulk,
  incremental, transform, and PostgreSQL-specific failure reports all pass with exact candidate
  identity and USD 0 local cost. An unchanged reporter-sidecar rerun retained the reports after the
  first successful Job's ephemeral-volume collection limitation. Both Jobs and all namespace,
  Secret, TLS, database, cluster, and temporary-tag resources were removed with zero Warning
  events. See `docs/evidence/phase8/2026-08-14/kubernetes-postgresql-scale-attempts.json`.
  Exact RC27 supersedes this launcher-scale evidence as recorded above; the RC22 history remains
  useful baseline evidence but no result was silently transferred.

- On 2026-08-14, exact RC22 passed approved local PostgreSQL bulk and incremental classes inside
  its source-free 2 CPU/512 MiB image against disposable TLS PostgreSQL 15.18. It processed
  500,000 narrow and 200,000 wide COPY rows, then applied a 3,000-row delta against 300,000 seed
  rows with an exact 301,500-row result and rejected cursor regression. A separate exact-candidate
  correctness fixture matched its approved normalized SHA-256 before and after replay. All three
  schemas and staging relations were removed; measured local service cost was USD 0. RC22 has no
  direct transport for a crossover comparison. RC24 supplies the corrected local crossover above,
  but the seven RC22 reports do not transfer and PostgreSQL hosted cost remains open.
  See `docs/evidence/phase8/2026-08-14/postgresql-bulk-throughput.json`.

- Exact RC22 also passed the PostgreSQL transform class: 100,000 facts joined 100 dimensions,
  produced exact ten-category aggregates, applied one update plus one insert through the
  incremental model, and passed 21 generic assertion executions. The final target held 100,001
  rows and cleanup was exact. The first attempt stopped before candidate transform code on a
  harness-only fixture escaping defect; the corrected retry and both cleanup outcomes are
  retained in `docs/evidence/phase8/2026-08-14/postgresql-transform-attempts.json`.

- Exact RC22 passed the PostgreSQL-specific failure class in 173 ms: pool exhaustion failed in a
  bounded 104 ms, a terminated connection was replaced, state operations recovered, and warehouse
  cancellation rolled back its transaction. Cleanup was exact and local cost was USD 0. Connector
  and launcher failure cases remain separate profile gates. See
  `docs/evidence/phase8/2026-08-14/postgresql-failure.json`.

- On 2026-08-14, exact private RC22 replaced RC21 on all five retained jobs through a saved
  `0 add / 5 change / 0 destroy` plan. Authenticated Salesforce manual/replay executions
  `dander-salesforce-accounts-rxvvd` and `dander-salesforce-accounts-xmm4r` produced equal counts;
  Scheduler-created Greenhouse execution `dander-greenhouse-public-v5ps9` also passed. All leases
  and staging relations were clean, 23 deployment checks passed, and the final 113-resource plan
  reported no drift. Provider charges remain pending, so this is not a passed cost qualification.
  See `docs/evidence/phase8/2026-08-14/gcp-native-profile.json`.

- Exact RC22 passed protected CI run `31825533602` and a local final-candidate repeat covering
  clean wheel/source installs, full runtime import, dependency and Git-history secret audits,
  Terraform/Helm, rootless read-only runtime checks, and HIGH/CRITICAL Trivy scans of
  infrastructure, the main image, and the OCI controller image. The post-merge regression suite
  passed 1,702 tests with 28 skips. See
  `docs/evidence/phase8/2026-08-14/rc22-local-audit.json`.

- On 2026-08-14, the D7 local hosted Control profile passed exact-digest HTTPS/OIDC, restart
  persistence, byte-equal render, stable second-up, rollback/restore, and cleanup qualification.
  The issuer was synthetic and disposable; no real-provider or cloud-hosted support is implied.
  Fresh retained-GCP stage-zero and current-equivalent RC21 platform plans then reported exact
  `No changes.` See `docs/evidence/local/2026-08-14/d7-control-plane.json`.

- On 2026-08-14, protected Dander `0.9.0rc20` published the D6 service/startup contract and D7
  local Compose assets from commit `75c5654e95439eaf18e90fbacc849799f4fe42b6`. The immutable
  `v0.9.0rc20` tag and trusted-publishing run `31815063258` produced public artifacts whose hashes
  and sizes matched PyPI. Fresh no-cache PyPI-only CLI, scaffold, project, import-origin, and
  Terraform validation passed outside every checkout. RC20 did not publish a current Dander
  container image and DRUFF-29 did not retain a durable image. The later local proof built and
  loaded exact reviewed images without changing this release's support status.

- On 2026-08-14, protected Dander `0.9.0rc19` published the complete deterministic
  `io.dander.control.contracts/v1` bundle from commit
  `cad383b8ac74e8ba0ce0b3b92c66b0a5a93a306b`. Trusted-publishing run `31785512985` produced the
  immutable `v0.9.0rc19` artifacts at bundle digest
  `695791dfda6058d68453d9e146146d5cdda1439d86c40a7ec249cb4e14a12be3`. A fresh PyPI-only
  installation outside any checkout matched all 37 manifest file hashes, generated and validated
  a source-free project, and passed Terraform validation. Druff may generate its D5 consumer only
  from this exact release artifact, never an unpublished checkout. RC19 packages all graph-store
  adapters, but only GCS is live-qualified; S3, Azure, and OCI remain unpromoted.

- On 2026-08-13, a read-only Phase 8 re-baseline confirmed all four retained schedules enabled and
  their latest executions successful. ServiceNow executions on 2026-08-10 and 2026-08-11 failed
  as `unexpected_error`; neither the durable ledger nor Cloud Logging retained a safe exception
  identity. That diagnosability defect blocks the current soak gate and is tracked separately from
  the normalized Phase 8 report contract. A later local Phase 8 slice implemented the exact
  AWS-native Fargate/Redshift/PostgreSQL/Glue/AWS-Secrets projection and scoped task policy. The
  implementation and RC25 candidate gates are complete; exact-objective live qualification remains
  open. See
  `docs/cloud-portability-phase8-qualification.md`.

- On 2026-08-13, public Dander `0.9.0rc17` passed the complete Phase 7 lifecycle for the named
  OCI Container Instances/PostgreSQL/PostgreSQL/no-catalog/OCI-Vault profile. One equal GAR/OCIR
  index passed manual success, scheduling, replay, overlap exclusion, cancellation, whole-task
  retry exhaustion, bounded logs, versionless application-secret rotation, immutable
  rollback/restoration, alarm-to-topic routing, cleanup, OCI no drift, and retained-GCP no drift.
  Direct OCI-to-Google identity remains unsupported and fails closed. OCI remains experimental
  pending Phase 8 qualification. See `docs/cloud-portability-oci-lifecycle-acceptance.md`.

- On 2026-08-12, the named Azure/Snowflake/PostgreSQL/Key-Vault profile passed the complete Phase 6
  lifecycle on one source-free, byte-identical GAR/ACR digest: preflight, manual and UTC-scheduled
  execution, replay, overlap fencing, interruption, retry exhaustion, alert routing, versionless
  secret rotation, immutable rollback, cleanup, and retained-GCP no drift. A separate
  public `0.9.0rc1` passed Azure-to-Google BigQuery access across credential refresh, GCP Secret
  Manager access, Dataplex read-back, revocation, isolated-GCP smoke, and cleanup. Azure remains experimental
  pending Phase 8 qualification. See
  `docs/cloud-portability-azure-lifecycle-acceptance.md`.

- On 2026-08-11, the Phase 5 common-scalar fixture passed BigQuery, PostgreSQL, Snowflake, and
  Redshift on protected-main commit `c0f3e2cb671eb6ddf1c34c60bc9e761d220cb9ad`. All four records
  produced one equal normalized hash after replay and verified owned cleanup. Provider account
  objects were removed, and fresh retained GCP stage-zero and current-equivalent platform plans
  each reported exact `No changes.` The platform proof reused the last accepted source-free
  deployment inputs; a current-default diagnostic that proposed only timeout/version-label changes
  was not applied.

- On 2026-08-10, public Dander `0.8.0rc8` passed the complete lifecycle gate for the named
  Fargate-to-BigQuery/GCP composition. The source-free, byte-identical GAR/ECR image completed
  manual, scheduled, replay, interruption, alert-routing, rollback, cleanup, and no-drift checks.
  Fargate remains experimental pending scale/profile qualification. See
  `docs/cloud-portability-fargate-lifecycle-acceptance.md`.

- On 2026-08-09, public Dander `0.8.0rc1` passed the isolated Phase 1B feasibility gate. One
  source-free multi-platform OCI index retained identical GAR/ECR and per-platform digests;
  Cloud Run completed on AMD64 and one ARM64 Fargate task queried BigQuery before and after a
  keyless Google credential refresh. All proof resources were destroyed and the isolated GCP
  platform finished at no drift. See `docs/cloud-portability-phase1b-acceptance.md`.
- On 2026-08-07, the isolated source-free `0.7.0rc2` image completed local/Cloud Run OCI parity,
  four-endpoint Salesforce ingestion, governed transforms/tests, replay, overlap skip,
  SIGTERM/SIGKILL recovery, Dataplex publication, lease and staging cleanup, and a final no-drift
  plan. ServiceNow completed its compatibility smoke in the same image. The bounded record is
  `docs/cloud-portability-phase1-acceptance.md`.

- On 2026-08-05, scheduled executions `dander-greenhouse-public-m2pz2`,
  `dander-hubspot-companies-cmdbz`, `dander-salesforce-accounts-jljwj`, and
  `dander-servicenow-incidents-6g72x` all completed successfully.
- The latest manual executable-graph run, `dander-greenhouse-graph-7gn9z`, completed successfully
  on 2026-08-04; its schedule remains intentionally paused.
- The latest exact-RC22 retained platform plan reported exactly `No changes.` after the approved
  five-job candidate rollout. Stage zero was not changed.
- Continue the 30-day operating record in GitHub issue #26. Do not close it until the diagnostic
  defect is corrected and the required clean observation evidence is current.

## Safety boundaries

- Repeat private operator inputs, including the failure-alert recipient and guarded billing
  account, on every full reconciliation; they are deliberately absent from public `dander.yaml`.
- Do not rotate or expose provider credentials, make the cost guard live, publish Dataplex, change
  schedules, alter retained data, or apply Terraform without explicit approval.
- NetSuite remains simulator-validated only. Workday has no real-tenant acceptance claim.
