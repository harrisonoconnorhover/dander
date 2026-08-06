# Session Resume — 2026-08-06

Read `HANDOFF.md`, `docs/decisions.md`, `docs/spec-alignment.md`, and
`docs/release-audit.md` before changing code or cloud resources.

## Public releases

- Dander `0.6.0rc1` is the newest public candidate. Dander `0.5.1` remains the latest stable alpha
  while candidate acceptance is in progress.
- Salesforce and ServiceNow connector plugins are public at `0.2.0` and require Dander `0.5.x`.
- Druff's fork contains Josh's reconciled graph-client ancestry and the later persistence,
  execution, catalog, operation-authoring, and deployment-preview work.

## Retained project

- Project `dander-proof-harrison-20260801`, region `us-central1`, remote states `dander/state` and
  `dander/bootstrap-admin/state` in `dander-proof-harrison-20260801-dander-state`.
- The source-free runtime is pinned to Dander `0.5.0` with Salesforce and ServiceNow plugins
  `0.2.0`, at immutable digest `sha256:3220623bf82a81d625db9e611c305694204e25ae312c06a8e8b1ea883bfd8995`.
  Dander `0.5.1` does not require a runtime rollout because its only change is catalog metadata.
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
