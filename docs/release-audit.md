# Dander Platform Release Audit

Audited on 2026-08-10 against the product promise in `steering/00-project-overview.md`.
“Live-proven” means the behavior was observed in a disposable provider account or retained GCP
project. “Implemented” means automated tests cover the contract while an optional provider or
cloud path remains outside the live proof.

## Requirement evidence

| Product requirement | Status | Current evidence |
|---|---|---|
| One CLI and typed project manifest | Live-proven | `dander.yaml` declares four daily connector pipelines and one executable graph; `dander validate`, provisioning, execution, and metadata inspection use the same CLI. |
| Batteries-included GCP infrastructure | Live-proven | Reviewed Terraform owns remote state, Artifact Registry, BigQuery datasets, per-pipeline IAM, Cloud Run jobs, Scheduler jobs, Secret Manager containers, failure alerts, and the optional cost guard. |
| Additive multi-pipeline hosting | Live-proven | Greenhouse, HubSpot, Salesforce, and ServiceNow coexist with distinct jobs, schedules, and runtime identities. The graph job is separately deployed and paused. |
| Independently installed connectors | Live-proven | Public Salesforce `0.3.1` and ServiceNow `0.2.2` packages are exact-pinned, discovered through Dander plugin API v1, installed into a source-free image, and exercised against disposable provider accounts. |
| Shared enterprise authentication | Live-proven for retained plugins | Dander core supplies Salesforce OAuth2 JWT and ServiceNow OAuth2 client credentials while Terraform grants each runtime access only to its declared Secret Manager resources. |
| Bounded ingestion and idempotent BigQuery writes | Live-proven for hosted SCD1 | Greenhouse, HubSpot, Salesforce, and ServiceNow have completed hosted ingestion and replay checks without duplicate destination keys; Salesforce applies its cursor server-side. |
| Owned transforms and tests | Live-proven | Retained pipelines execute Dander's `ref()` DAG and declared BigQuery assertions after ingestion. |
| Durable state and optional read capabilities | Live-proven | Run history, leases, watermarks, count, connection-check, and targeted-read paths were exercised for the relevant retained plugins without provider write-back. |
| Single metadata spine | Live-proven | The run ledger and catalog store pipeline lifecycle, source/model schema, lineage, tests, and governed metrics in BigQuery; Dataplex publication remains optional. |
| Canonical visual authoring | Live-proven for the bounded graph slice | Druff opens and saves canonical `PipelineGraph`, discovers presentation-safe connector and operation descriptors, starts an already-deployed run, and previews a non-applyable full-manifest plan through Dander's loopback service. |
| Infrastructure reconciliation safety | Live-proven | The retained platform plan after Druff deployment reported `0` add, `0` change, and `0` destroy across `111` no-op resources. |

## Current release and deployment record

- Public Dander beta: `0.8.0rc8`; public Salesforce connector: `0.3.1`; public ServiceNow connector:
  `0.2.2`.
- Isolated portability acceptance used Dander `0.7.0rc2` and the public plugin candidates in a
  source-free image. The same accepted OCI content passed local and Cloud Run conformance.
  Salesforce ingested all four endpoints, published five governed models and Dataplex metadata,
  replayed without duplicate keys or cursor regression, skipped an overlapping run, recovered
  from a bounded interruption, released its lease, removed staging, and finished with Terraform
  no-drift. See `docs/cloud-portability-phase1-acceptance.md`.
- Public Dander `0.8.0rc1` passed the separate Phase 1B feasibility gate: one source-free
  multi-platform index remained byte-identical across GAR and ECR, completed Cloud Run
  conformance on AMD64, and queried BigQuery from one ARM64 Fargate task before and after keyless
  Google credential refresh. All proof resources were removed, and Fargate remains experimental.
  See `docs/cloud-portability-phase1b-acceptance.md`.
- Public Dander `0.8.0rc8` passed the complete lifecycle gate for the named
  Fargate-to-BigQuery/GCP composition: manual and scheduled execution, replay, interruption,
  alert routing, image rollback, cleanup, and four final no-drift plans. Fargate remains
  experimental pending scale/profile qualification. See
  `docs/cloud-portability-fargate-lifecycle-acceptance.md`.
- Retained source-free Dander image: `sha256:68e112c43b365018b735be7934446e15dfe6169fc64062b62b8bb97ea4f93b96`,
  built with Dander `0.7.1`, Salesforce `0.3.1`, and ServiceNow `0.2.2`.
- Retained Druff image: `sha256:a5e255d6…871c`; public static URL:
  <https://dander-druff-yos2b3gbca-uc.a.run.app>.
- The four scheduled connector executions on 2026-08-05 completed successfully. Those executions
  preceded the final stable-image reconciliation; observation of subsequent scheduled runs remains
  in the active operator soak.
- Greenhouse graph execution `dander-greenhouse-graph-7gn9z` completed successfully on 2026-08-04;
  its 13:00 schedule remains intentionally paused.

## Release boundaries

- Dander provisions inside an existing billing-linked GCP project; it does not create a project or
  attach billing. Its managed cost guard is simulation-first and is not a spending cap.
- The `0.6.0` Salesforce example supports read-only Accounts, Contacts, Opportunities, and
  Users; the retained soak still exercises its existing Accounts pipeline. ServiceNow supports
  one read-only incidents slice. Provider write-back and broad object/table coverage are not
  claimed.
- NetSuite is simulator-validated only; Workday lacks real-tenant acceptance. Live Storage Write
  and optional Dataplex publication are not implied by the retained proof.
- Druff is a public static browser shell. Its privileged operations remain in an operator-started,
  exact-origin Dander loopback service; Druff cannot apply Terraform or write the project manifest.

## Verdict

Dander demonstrates its intended beta vertical slice: one manifest and CLI reconcile an owned
GCP platform, independently installed connectors ingest into BigQuery, Dander transforms and tests
the data, one metadata spine records what happened, and Druff authors and operates a bounded graph
without becoming a second runtime. Production hardening and broader connector coverage remain
future work.
