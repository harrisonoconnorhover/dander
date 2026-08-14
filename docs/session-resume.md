# Session Resume — 2026-08-14

Read `HANDOFF.md`, `docs/decisions.md`, `docs/spec-alignment.md`, and
`docs/release-audit.md` before changing code or cloud resources.

## Public releases

- Dander `0.9.0rc19` is the current public beta.
- Salesforce `0.3.1` and ServiceNow `0.2.2` are the current stable connector releases for Dander
  `0.7.x`; their accepted release candidates remain recorded in the Phase 1 evidence.
- Druff's fork contains Josh's reconciled graph-client ancestry and the later persistence,
  execution, catalog, operation-authoring, and deployment-preview work.

## Retained project

- Project `dander-proof-harrison-20260801`, region `us-central1`, remote states `dander/state` and
  `dander/bootstrap-admin/state` in `dander-proof-harrison-20260801-dander-state`.
- The source-free runtime is pinned to Dander `0.7.1`, Salesforce `0.3.1`, and ServiceNow `0.2.2`
  at immutable digest `sha256:68e112c43b365018b735be7934446e15dfe6169fc64062b62b8bb97ea4f93b96`.
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

- On 2026-08-14, protected Dander `0.9.0rc19` published the complete deterministic
  `io.dander.control.contracts/v1` bundle from commit
  `cad383b8ac74e8ba0ce0b3b92c66b0a5a93a306b`. Trusted-publishing run `31785512985` produced the
  immutable `v0.9.0rc19` artifacts at bundle digest
  `695791dfda6058d68453d9e146146d5cdda1439d86c40a7ec249cb4e14a12be3`. A fresh PyPI-only
  installation outside any checkout matched all 37 manifest file hashes, generated and validated
  a source-free project, and passed Terraform validation. Druff may generate its D5 consumer only
  from this exact release artifact, never an unpublished checkout. RC19 packages all graph-store
  adapters, but only GCS is live-qualified; S3, Azure, and OCI remain unpromoted.

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
- The latest retained stage-zero and current-equivalent platform plans reported exactly
  `No changes.` after the OCI proof. No
  retained-project apply occurred.
- Continue the 30-day operating record in GitHub issue #26. The next normal scheduled runs are the
  remaining observation point for the reconciled stable image.

## Safety boundaries

- Repeat private operator inputs, including the failure-alert recipient and guarded billing
  account, on every full reconciliation; they are deliberately absent from public `dander.yaml`.
- Do not rotate or expose provider credentials, make the cost guard live, publish Dataplex, change
  schedules, alter retained data, or apply Terraform without explicit approval.
- NetSuite remains simulator-validated only. Workday has no real-tenant acceptance claim.
