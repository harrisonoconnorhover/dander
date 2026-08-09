# Cloud portability Phase 1 acceptance

Accepted on 2026-08-07 against public Dander `0.7.0rc2`, Salesforce connector
`0.3.1rc1`, and ServiceNow connector `0.2.2rc1`. The proof used a generated source-free
project in the isolated disposable GCP project; both schedules remained paused. The retained
project was not changed.

## Artifact and installation

- Public packages installed outside every repository checkout without copying `src/`.
- Exact plugin installation retained the running Dander candidate instead of downgrading it.
- The source-free image was built once with OCI metadata, SBOM/provenance, a non-root user, and a
  read-only-root-filesystem contract.
- Accepted OCI index digest:
  `sha256:f807318db2ee9b4ac56690779fdb27df59b8732815e8d294e43c4166fae9312a`.
- Local inspection and Cloud Run resolved the same Linux/AMD64 content. Local and Cloud Run
  conformance emitted the same normalized `runtime.started` and `runtime.completed` outcome.

## Runtime behavior

- ServiceNow execution `dander-servicenow-incidents-xqtgf` ingested 67 incidents, built one model,
  passed four assertions, and published one catalog asset.
- Salesforce execution `dander-salesforce-plugin-w9jvt` ingested five inclusive-boundary records
  across Accounts, Contacts, Opportunities, and Users, built five models, passed 35 assertions,
  and published five catalog assets.
- Salesforce replay `dander-salesforce-plugin-lcjst` repeated that boundary safely. Raw table
  counts remained 17, 21, 32, and 8; every table retained zero duplicate IDs; no cursor regressed.
- Concurrent execution `dander-salesforce-plugin-f8mjs` emitted a successful terminal
  `status=skipped` while the replay held the lease.
- A 60-second Cloud Run timeout interrupted `dander-salesforce-plugin-svc4l` after lease
  acquisition. Run history recorded sanitized `interrupted_run`, the lease cleared, and no staging
  table remained. Recovery execution `dander-salesforce-plugin-6rcdx` then completed all five
  models, 35 assertions, and five catalog assets without data or cursor change.

## Signal and reconciliation evidence

- Cloud Run delivered a catchable termination and received a retryable `interrupted_run` terminal
  event before stopping the task.
- The accepted container was also killed locally with SIGKILL after `runtime.started`; it exited
  137 and emitted no false terminal event.
- The next real Cloud Run lease acquisition reconciled a representative orphaned active run to
  `failed/interrupted_run` with the bounded summary, “A later execution acquired the expired
  pipeline lease.”

## Final state

- Salesforce and ServiceNow leases are released; no run-scoped staging residue exists.
- Alerts, datasets, secrets, IAM, Druff, and paused schedules remain present and unchanged.
- The final manifest-aware Terraform plan reported exactly `No changes.`
- No credential, provider response, source row, Terraform state, or alert address is retained in
  this record.

Phase 1 is accepted. The separate [Phase 1B artifact-copy and keyless AWS-to-BigQuery
proof](cloud-portability-phase1b-acceptance.md) also passed; neither record makes an AWS support
claim.
