# Session Resume — 2026-08-09

Read `HANDOFF.md`, `docs/decisions.md`, `docs/spec-alignment.md`, and
`docs/release-audit.md` before changing code or cloud resources.

## Public releases

- Dander `0.7.1` is the current public beta.
- Salesforce `0.3.1` and ServiceNow `0.2.2` are the current stable connector releases for Dander
  `0.7.x`; their accepted release candidates remain recorded in the Phase 1 evidence.
- Druff's fork contains Josh's reconciled graph-client ancestry and the later persistence,
  execution, catalog, operation-authoring, and deployment-preview work.

## Retained project

- Project `dander-proof-harrison-20260801`, region `us-central1`, remote states `dander/state` and
  `dander/bootstrap-admin/state` in `dander-proof-harrison-20260801-dander-state`.
- The accepted source-free runtime is pinned to Dander `0.7.1rc1`, Salesforce `0.3.1`, and
  ServiceNow `0.2.2`, at immutable digest
  `sha256:5f9db33a09cd486a5e426f3012e3925bb10b2ea23336b9a2c61460d84f5bb7d2`.
  Stable `0.7.1` retains identical runtime source and requires only the final package-version image.
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

- On 2026-08-09, retained manual executions completed for Greenhouse
  (`dander-greenhouse-public-rh4cc`), HubSpot (`dander-hubspot-companies-sq8d6`), Salesforce
  (`dander-salesforce-accounts-5pw68`), ServiceNow (`dander-servicenow-incidents-qhnvn`), and the
  graph (`dander-greenhouse-graph-djf4t`). Salesforce passed 35 assertions, published five assets,
  retained monotonic cursors, and had zero duplicate IDs across all four endpoints.
- The accepted timeout-recovery run left no active lease or run-scoped staging. All five failure
  alerts remain enabled, four schedules are restored, and the graph remains paused.
- Refreshed final Terraform plans reported no changes across 28 stage-zero and 113 platform
  resources.

- On 2026-08-07, the isolated source-free `0.7.0rc2` image completed local/Cloud Run OCI parity,
  four-endpoint Salesforce ingestion, governed transforms/tests, replay, overlap skip,
  SIGTERM/SIGKILL recovery, Dataplex publication, lease and staging cleanup, and a final no-drift
  plan. ServiceNow completed its compatibility smoke in the same image. The bounded record is
  `docs/cloud-portability-phase1-acceptance.md`.

- Continue the 30-day operating record in GitHub issue #26. The next normal scheduled runs are the
  remaining observation point for the reconciled stable image.

## Safety boundaries

- Repeat private operator inputs, including the failure-alert recipient and guarded billing
  account, on every full reconciliation; they are deliberately absent from public `dander.yaml`.
- Do not rotate or expose provider credentials, make the cost guard live, publish Dataplex, change
  schedules, alter retained data, or apply Terraform without explicit approval.
- NetSuite remains simulator-validated only. Workday has no real-tenant acceptance claim.
