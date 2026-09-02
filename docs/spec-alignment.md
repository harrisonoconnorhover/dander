# Upstream Spec Alignment

This ledger maps `steering/00-project-overview.md` to current code and observed runtime state.

| Module | Status | Current evidence | Deliberate boundary |
|---|---|---|---|
| Security | Live-proven | Per-pipeline Secret Manager bindings, separate bootstrap/runtime/scheduler identities, guarded billing visibility, exact-origin loopback Druff access, and no service-account keys | Other private vendors require separately approved tenant credentials |
| Hybrid ingestion | Live-proven | Config-driven REST/dlt and hand-rolled `Source` paths; hosted Greenhouse, HubSpot, Salesforce, ServiceNow, Odoo, and executable-graph proofs completed | Workday and NetSuite are not real-tenant validated |
| Connector plugins | Live-proven | Stable Salesforce `0.3.1` and ServiceNow `0.2.2` preserve the exact accepted candidate behavior, register through API v1, and expose presentation-safe discovery plus optional reads | The built-in Salesforce fallback is deprecated; no marketplace service or provider write-back |
| BigQuery writer | Live-proven for hosted SCD1 and graph replace | Bounded batches, declared schemas, replay-safe SCD1, fenced DML publication, and staged executable-graph replacement | Direct cloud replace and live Storage Write/SCD2 are outside the retained proof |
| Transform | Live-proven | Restricted `ref()` DAG, topological execution, materialization, generic tests, and the advertised graph-operation subset ran through Dander | Arbitrary browser-executed code and provider mutations are excluded |
| Metadata spine | Live-proven | Atomic per-pipeline catalog snapshots and run history expose sources, models, columns, lineage, tests, metrics, counts, and lifecycle state | Dataplex remains an optional projection and was not mutated in the retained proof |
| Bootstrap CLI | Live-proven | `dander init` owns stage zero, image publication, full platform planning, and reviewed apply inside existing projects; retained Terraform currently has no drift | Project creation and billing linkage remain administrator prerequisites |
| Orchestration/state | Live-proven | Five retained independent schedules, now all paused after the operator trial, plus leases, fencing tokens, cursor compare-and-set, staging cleanup, and exact-job failure alerts | Schedule-miss and freshness SLOs are not implemented |
| Druff | Live-proven for bounded authoring/operations | Hosted static UI, canonical graph persistence, dynamic connector/operation discovery, validation, manual run/status, and non-applyable full-manifest preview | No manifest write-back, Terraform apply, hosted privileged API, or provider write-back |
| Release evidence | Live-proven | Public source-free Dander and plugin installs, disposable provider/GCP acceptance, the completed 2026-08-02 through 2026-09-01 retained-project operator trial, protected CI, and clean Terraform reconciliation | The closed trial retains an external ServiceNow PDI limitation and does not complete Phase 8 qualification |

Detailed execution identifiers and release boundaries are in [`release-audit.md`](release-audit.md).
