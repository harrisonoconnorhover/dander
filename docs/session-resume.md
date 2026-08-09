# Session Resume — 2026-08-07

Read `HANDOFF.md`, `docs/decisions.md`, `docs/spec-alignment.md`, and
`docs/release-audit.md` before changing code or cloud resources.

## Public releases

- Dander `0.8.0rc3` is the current public beta candidate.
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
- The most recent reviewed platform plan reported `0` add, `0` change, and `0` destroy across
  `111` no-op resources after Druff deployment.
- Continue the 30-day operating record in GitHub issue #26. The next normal scheduled runs are the
  remaining observation point for the reconciled stable image.

## Safety boundaries

- Repeat private operator inputs, including the failure-alert recipient and guarded billing
  account, on every full reconciliation; they are deliberately absent from public `dander.yaml`.
- Do not rotate or expose provider credentials, make the cost guard live, publish Dataplex, change
  schedules, alter retained data, or apply Terraform without explicit approval.
- NetSuite remains simulator-validated only. Workday has no real-tenant acceptance claim.
