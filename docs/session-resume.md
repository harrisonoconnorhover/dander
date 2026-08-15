# Session Resume — 2026-08-14

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

- Azure and OCI interactive authentication were restored and verified through provider APIs on
  2026-08-14. Azure contains no Dander-named resource or resource group. OCI retains its accepted
  Phase 7 foundation and private image history but has zero active Container Instances. The OCI CLI
  omitted the session profile's user field; it was restored from the signed token subject without
  recording an identifier or credential. No cloud mutation occurred. Credentials no longer block
  these providers, but protected review and one replacement candidate still precede any Phase 8
  live run. See the Azure and OCI credential-restoration records under
  `docs/evidence/phase8/2026-08-14/`.

- AWS access was restored on 2026-08-14. Exact RC22 was copied byte-identically to private ECR,
  and a pre-approved 28-resource Redshift Serverless/PostgreSQL/Glue/Secrets data plane was created
  under a USD 3 allocation. Read-only image inspection then found no packaged AWS deployment, so
  no Fargate plan, task, or pipeline ran. The exact 28-resource destroy completed, qualification
  state and inventories are empty, and AWS D7 was unchanged. Provider cost remains pending. A local
  correction projects the selected non-secret platform overlay at launch. Completion review then
  found missing self-scoped database egress in the disposable fixture; its correction passes the
  focused Terraform contract, but protected CI/review and a replacement candidate are required
  before live qualification resumes. See
  `docs/evidence/phase8/2026-08-14/aws-native-profile-attempt.json`.

- Private arm64 Dander `0.9.0rc23` at commit `2455fc34d4503863060b7bac873be36319c13e4f`
  was published only to the private qualification registry at index `sha256:8bd35188…3064`. It
  passed exact artifact/runtime/security preflight and the pre-approved local DIRECT-to-COPY
  crossover against TLS PostgreSQL 15.18. DIRECT tied COPY only at 10 rows, but completion review
  found its 1,400-byte threshold omitted field-name bytes counted by the writer and would select
  COPY. The corrected harness derives 1,490 bytes and buffers bounded lookahead before opening a
  transaction. RC23's threshold objective is invalid; the replacement candidate must rerun it.
  See `docs/evidence/phase8/2026-08-14/phase8-completion-review.json`.

- Exact RC22 passed five normalized Kubernetes launcher classes on kind 1.32.2 under a 2 CPU/512
  MiB limit, 600-second deadline, and zero retries against TLS PostgreSQL 15.18. Correctness, bulk,
  incremental, transform, and PostgreSQL-specific failure reports all pass with exact candidate
  identity and USD 0 local cost. An unchanged reporter-sidecar rerun retained the reports after the
  first successful Job's ephemeral-volume collection limitation. Both Jobs and all namespace,
  Secret, TLS, database, cluster, and temporary-tag resources were removed with zero Warning
  events. See `docs/evidence/phase8/2026-08-14/kubernetes-postgresql-scale-attempts.json`.

- On 2026-08-14, exact RC22 passed approved local PostgreSQL bulk and incremental classes inside
  its source-free 2 CPU/512 MiB image against disposable TLS PostgreSQL 15.18. It processed
  500,000 narrow and 200,000 wide COPY rows, then applied a 3,000-row delta against 300,000 seed
  rows with an exact 301,500-row result and rejected cursor regression. A separate exact-candidate
  correctness fixture matched its approved normalized SHA-256 before and after replay. All three
  schemas and staging relations were removed; measured local service cost was USD 0. RC22 has no
  direct transport for a crossover comparison. RC23 supplies the separate local crossover above,
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
  AWS-native Fargate/Redshift/PostgreSQL/Glue/AWS-Secrets projection and scoped task policy, but
  protected review, candidate creation, and live qualification remain open. See
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
