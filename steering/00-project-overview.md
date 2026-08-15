# Project Overview — Dander

> **What every agent should know before touching this repo.** This is the north star.
> When a product decision is made, append it to the Decision Log at the bottom — this file is
> the single source of truth for "why is it this way."

## One-liner

Dander is an open-source, cloud-selectable EL(T) suite for reading from SaaS systems
(Salesforce, Workday, Greenhouse, NetSuite, Marketo, Xactly, …) and ingesting them
**efficiently and idempotently into an explicitly supported warehouse profile** — a focused,
self-owned replacement for Informatica, and a customizable stand-in for dbt/SQLMesh
transformation. GCP, Cloud Run, and BigQuery remain the primary compatibility profile.

## Why this exists

- **Informatica is painful** and expensive; we want to own the tooling.
- **dbt Core is free but not fully ours**, and the transformation OSS landscape consolidated
  under one vendor (Fivetran acquired Census, Tobiko/SQLMesh, and dbt Labs across 2025–2026).
  Owning the transform layer removes vendor-consolidation risk.
- The proven production profile runs on **GCP** and lands in **BigQuery**. Portability is added
  through named, separately qualified platform profiles; a new provider is never implied by a
  generic interface or local mock alone.

## Modules

| Module | Responsibility |
|---|---|
| **Security** | GCP Secret Manager backing store; pluggable auth **strategy** per system (OAuth2 client-creds / JWT, OAuth1 TBA, API-key/basic). Token caching + refresh + **audit logging** of credential access. |
| **Ingestion (hybrid)** | Each source is a **config object** (base URL, auth ref, endpoints, pagination, incremental cursor, field mappings). Standard REST sources run on **dlt**; enterprise sources (Workday/NetSuite/Xactly) use hand-rolled `EnterpriseSource` extractors. Both behind the `Source` interface. Rate limiting/backoff per source; inferred type casting to BigQuery types with per-field overrides. |
| **BigQuery Writer** | Multiple write patterns: SCD1 (MERGE), SCD2 (versioned rows), daily snapshot (partitioned append), incremental (watermark). Storage Write API vs load jobs per workload. |
| **Transform** | dbt-replacement: Jinja2 `ref()` templating → parsed dependency DAG → topological execution. Materializations reuse the Writer patterns. Generic tests (not-null/unique/accepted-values/relationships). One YAML per model feeds SQL + Dataplex catalog aspects + semantic registry. |
| **Bootstrap CLI** | pip-installable; wraps **Terraform** to provision a selected, named platform profile. The current compatibility profile provisions Secret Manager, service accounts + least-privilege IAM (Workload Identity Federation), Cloud Run jobs, and BigQuery datasets. |
| **Orchestration/State** | `PipelineExecutor` owns ingest → transform/tests → metadata and one truthful lifecycle record; Cloud Scheduler invokes isolated Cloud Run jobs; BigQuery/SQLite persist cursors, run history, and atomic catalog snapshots. |
| **Metadata spine** | One typed source/model definition projects source endpoints, models, columns, lineage, tests, governed metrics, local JSON, optional Dataplex aspects, and the durable `dander_meta` catalog. |

## Scope discipline (non-goals)

- Not a general-purpose "everything" tool. We read APIs into qualified warehouse profiles; the
  GCP/BigQuery profile remains the compatibility baseline until another profile passes its live
  release gate.
- Do not claim a Cartesian mix of launchers, warehouses, state backends, catalogs, and secret
  providers. Only named combinations in the tested compatibility matrix are supported.
- Prove the pattern on **low-friction sources first** (Greenhouse, Marketo) end-to-end
  before tackling ugly auth/data shapes (Workday, NetSuite).
- Borrow vs. build is decided per-module in the Decision Log — don't reinvent pagination/retry
  if a library (e.g. `dlt`) earns its place; the *differentiated* layers are Security + Writer + Transform.

## Tech stack

- **Python 3.12+** — primary application language. See `languages/python.md`.
- **BigQuery Standard SQL** — the current transform compatibility dialect. See `languages/sql.md`.
- **Terraform (HCL)** — provider-separated infrastructure-as-code. See `languages/terraform.md`.
- **GCP Secret Manager** — the current compatibility-profile secret store; later profiles must use
  an explicitly qualified reference-based secret provider.

## Compliance note (read before open-sourcing)

This touches HR/comp data (Workday, Xactly) and customer data (Salesforce, NetSuite) at a
regulated company. **Clear internal OSS/legal review before publishing.** Treat this as a
blocking gate on any public release, separate from engineering readiness.

**Local interpretation recorded 2026-07-30:** the systems above are hypothetical connector
categories. They do not establish that Dander derives from, connects to, or contains data from an
existing company. Do not infer employer ownership, regulated-company affiliation, customer
records, or HR records. Provenance/privacy review becomes applicable if actual employer-owned
material, credentials, or non-public data is introduced.

---

## Decision Log

Append newest at top. Format: `- YYYY-MM-DD — decision — rationale`.

- 2026-08-15 — **Fargate prepares Google identity only for a declared Google federation** — the
  AWS-native profile keeps its renewable ECS task role ambient, while Fargate-to-GCP deployments
  continue to build keyless Google credentials when their federation settings are projected.
  Partial federation configuration still fails closed.
- 2026-08-07 — **Dander becomes cloud-selectable without weakening the GCP contract** — a
  versioned OCI runtime and named deployment profiles will separate logical pipelines from cloud
  projection. GCP/Cloud Run/BigQuery remains the primary compatibility profile, and each added
  combination stays unsupported until its adapter, identity, launcher, and live proof pass.
- 2026-08-02 — **Workday acceptance begins with a three-operation RaaS contract** — token
  issuance plus workers and organizations custom reports are simulated over real loopback HTTP;
  tenant auth, prompt aliases, and permissions remain unproven until a narrow live acceptance.
- 2026-07-31 — **One executor owns end-to-end success** — named pipeline history cannot claim
  success after ingestion alone; the terminal record includes transform/test and metadata stages,
  aggregate counts, and a failure-stage marker without retaining data or exception text.
- 2026-07-31 — **The metadata spine is a durable atomic snapshot** — BigQuery (`dander_meta`) and
  SQLite retain one current per-pipeline manifest covering sources, models, lineage, tests, and
  governed metrics; local JSON and Dataplex are projections of the same canonical assets.
- 2026-07-31 — **`dander init` owns both bootstrap stages** — the hardened GCS backend is the sole
  imperative stage-zero exception and is immediately imported; the remaining state, identity,
  image, datasets, jobs, schedules, secrets, and simulation-first cost guard remain declarative.

- 2026-07-31 — **Hosted orchestration is additive and project-defined** — versioned
  `dander.yaml` pipeline definitions drive both local execution and Terraform expansion; each
  pipeline owns its job, schedule, identities, and secret bindings while sharing the runtime image,
  datasets, and metadata spine. Adding a connector must not repurpose or replace another pipeline.

- 2026-07-29 — **The metadata spine is deterministic and local-first** — one validated model YAML
  projects to transforms/tests, stable semantic JSON, and reusable Dataplex system aspects;
  catalog mutation requires an explicit flag because stored aspect metadata is billable.
- 2026-07-29 — **The first transform slice builds and tests public jobs as views/tables** — typed
  YAML, fail-closed DAG resolution, restricted `ref()` rendering, and generic assertions prove the
  owned raw-to-staging path; incremental materialization waits for an explicit idempotent contract.
- 2026-07-29 — **The first hosted runtime is a daily, paused-first Cloud Run Job** — the public
  connector needs no stored credential; separate least-privilege runtime and scheduler identities,
  an immutable image digest, and bounded image retention minimize both access and cost exposure.
- 2026-07-29 — **Greenhouse has separate public and private connector paths** — the public Job
  Board connector gives a real, credential-free first run; the canonical private connector uses
  Harvest v3 OAuth client credentials, while v1 remains explicitly legacy only until its announced
  2026-08-31 shutdown.
- 2026-07-29 — **The cost guard is simulation-first, idempotent, and budget-specific** — malformed
  or unrelated notifications are ignored; deployment proves the trigger without mutation before
  live mode is enabled, and a dedicated service account can only manage project billing and logs.
- 2026-07-29 — **Billing-linked testing gets a fail-closed guardrail preflight, not a “hard cap”**
  — the production SCD1/Secret Manager path may run only after Dander observes billing enabled, a
  project-scoped budget no greater than $5, 80%/100% thresholds, and conventional Pub/Sub wiring;
  billing latency and subscriber health remain explicit residual risks.
- 2026-07-29 — **Strict $0 sandbox = billing-disabled BigQuery + full replacement + local state**
  — BigQuery Sandbox disallows DML, so this explicitly non-production mode verifies billing is
  disabled before creating anything, uses load jobs instead of `MERGE`, and never resumes from its
  diagnostic SQLite cursor. Terraform, Secret Manager, GCS, and Cloud Run stay out of this mode.
- 2026-07-29 — **First runnable slice = Greenhouse → BigQuery SCD1** — proves the documented
  low-friction-source path before enterprise connectors; dlt owns REST pagination, Dander owns
  audited secret resolution, staging-table idempotency, and post-write watermark commits.
- 2026-07-29 — **Endpoint cursor field and request parameter are distinct** — source responses and
  request filters can use different names (Greenhouse `updated_at` / `updated_after`), so connector
  config carries both instead of overloading one ambiguous string.
- 2026-07-09 — **Ingestion = hybrid** — dlt for standard REST sources (pagination/retry/incremental/schema-evolution/BQ-load); hand-rolled `EnterpriseSource` for Workday/NetSuite/Xactly where dlt's generics fall short. Both implement the `Source` interface so downstream layers are path-agnostic.
- 2026-07-09 — **Transform = own engine** — Jinja2 `ref()` → sqlglot DAG → topological execution + generic tests, reusing the writer's materializations. Ownership/customization was the original motivation; the metadata spine is native to it; avoids the Fivetran-consolidation risk of dbt/SQLMesh.
- 2026-07-09 — **Stack = Python 3.12** (app + Typer CLI), **BigQuery SQL** (transforms), **Terraform/HCL** (infra), **YAML** (config). Package: src-layout, hatchling build, uv-managed.
- 2026-07-09 — **Project skeleton scaffolded** — interface-first stubs across all modules; implementations tracked as tickets and built by the workforce.
- 2026-07-09 — **Orchestration = automated Workflow** (`.claude/workflows/feature.js`) — user wants hands-off fan-out of the product→design→code→review loop.
- 2026-07-09 — **Tickets = local markdown** under `tickets/` — git-trackable, no external deps.
- 2026-07-09 — **IaC = Terraform (HCL)** — mature multi-cloud providers; adding a cloud later is a new module, not a rewrite.
- 2026-07-09 — **Governance-first bootstrap** — steering files + agent workforce built before any platform code.
