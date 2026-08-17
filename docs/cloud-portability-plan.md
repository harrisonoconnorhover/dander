# Cloud Portability and First-Class Platform Roadmap

Status: active roadmap; Phases 0 through 7 exit gates satisfied; Phase 8 in progress

Initial planning baseline: `origin/main` at `9dc5562` (`0.6.0rc1`)

Phase 5 implementation checkpoint: protected `main` at
`3a10c121d1d77b7a5074ce7d07a3c9d6b7cb7e1e` after PRs #178 and #179

Phase 5 exit-evidence candidate: protected `main` at
`c0f3e2cb671eb6ddf1c34c60bc9e761d220cb9ad` after PRs #184 and #185. The shared live records are
stored under `docs/evidence/warehouse-correctness/2026-08-11/` and reconciled through protected
evidence PR #186.

Phase 6 implementation checkpoint: protected `main` at
`eb074c58a9b3d8c1296c28849639a04c07fdb4bf` after PRs #189 through #208. Public candidate
`0.9.0rc1` was tagged from protected `main` at `2b90f7ad9d02ad303d3543f1e27febc7193e9c82`.
The sanitized lifecycle record was merged through PR #210 at protected `main`
`0197d931ad1b86f3101a5f2d51170a77c23fe1b7` and is stored under
`docs/evidence/azure/2026-08-11/`.

Phase 7 lifecycle evidence was reconciled through PR #250 at protected `main`
`536b31b701a67a5b7eeb68e09e1d87a4c59898f9`. Public `0.9.0rc17` passed the named OCI profile,
and its sanitized record is stored under `docs/evidence/oci/2026-08-13/`.

Prepared: 2026-08-06; reconciled: 2026-08-13

The baseline descriptions below record the conditions this roadmap was designed to change. They
are retained as architectural rationale rather than claims about the current implementation. The
phase ledger and compatibility documentation are the authoritative current-status record.

## 1. Outcome

Dander will ship one documented, immutable OCI runtime that can execute the same logical pipeline
under Cloud Run, ECS/Fargate, Kubernetes, Azure Container Apps Jobs, or Oracle Cloud Container
Instances. A project will select its warehouse, execution-state backend, catalog publisher, secret
source, and launcher independently through validated configuration.

The complete first-class support target is:

| Capability | Required providers |
|---|---|
| Warehouse execution | BigQuery, Snowflake, Redshift, PostgreSQL |
| Durable execution state | BigQuery, PostgreSQL |
| Cloud catalog | Dataplex, AWS Glue Data Catalog, none |
| Runtime secret resolution | Environment, GCP Secret Manager, AWS Secrets Manager |
| Launcher-native secret projection | GCP Secret Manager, AWS Secrets Manager, Azure Key Vault, OCI Vault |
| Launching | Cloud Run Jobs, ECS/Fargate, Kubernetes/Helm, Azure Container Apps Jobs, OCI Container Instances |
| Portable artifact | Documented OCI image and invocation/result contract |

This roadmap preserves the existing GCP/BigQuery behavior while turning it into the first
conforming implementation. Provider support is added through complete vertical slices, not by
shipping unfinished stubs for every provider simultaneously.

## 2. Definition of “first class”

A provider is not first class merely because the container starts or a client library connects.
Each provider must satisfy every applicable item below.

### 2.1 Warehouse

- Resolve and validate a provider-specific connection without exposing credentials.
- Create or validate Dander-owned raw, staging, transform, and control namespaces.
- Support `replace`, SCD1, SCD2, snapshot, and incremental write modes.
- Support declared schema creation and the common additive-evolution contract.
- Support bounded ingestion without buffering a complete endpoint in memory.
- Use the provider’s bulk path for large loads and a bounded direct path for small loads.
- Materialize tables, views, and incremental models with provider-specific SQL.
- Run not-null, unique, accepted-values, and relationship assertions.
- Preserve idempotency across launcher retries and process interruption.
- Enforce a target-side fencing token at the final publication boundary.
- Emit normalized row, byte, duration, retry, query/job, and cost-attribution metadata.
- Pass unit, adapter-conformance, live correctness, interruption, replay, and scale tests.
- Have setup, permissions, operations, upgrade, rollback, and limitation documentation.

### 2.2 State backend

- Persist leases, fencing tokens, watermarks, run history, failure summaries, and metadata snapshots.
- Use backend server time for lease expiry.
- Acquire, heartbeat, and release a lease conditionally and atomically.
- Increment fencing tokens monotonically.
- Compare-and-set watermarks atomically.
- Reconcile runs left active by SIGKILL or platform termination.
- Apply versioned, idempotent schema migrations.
- Retain no source rows, secret values, or unrestricted exception text.
- Pass the same state-conformance suite for BigQuery and PostgreSQL.

### 2.3 Catalog provider

- Consume the same canonical metadata manifest used by execution and the local registry.
- Create or update only Dander-owned catalog fields, aspects, tags, or parameters.
- Preserve unrelated provider metadata.
- Read the published object back and normalize it for deterministic comparison.
- Treat `none` as a complete provider that performs no cloud mutation while retaining the local and
  durable semantic snapshot.
- Never delete catalog resources by default.

### 2.4 Launcher

- Provision or install from reviewed infrastructure as code.
- Run the exact OCI manifest digest tested by the release.
- Support one-off, scheduled, paused, resumed, status, log, cancel, and replay operations.
- Supply workload identity, network placement, secret references, environment, CPU, memory,
  ephemeral storage, deadline, retry count, and parallelism.
- Keep scheduling retries distinct from runtime retries and expose both attempt numbers.
- Capture structured runtime output and provider-native execution status.
- Publish logs, metrics, failure alerts, and run correlation identifiers.
- Support immutable upgrades and rollback to the prior digest.
- Pass deployment verification, manual execution, scheduled execution, overlap, SIGTERM, SIGKILL,
  credential refresh, and no-change reconciliation tests.
- Document provider limits and unsupported execution-projection fields.

## 3. Initial baseline and constraints

The implementation started from a proven GCP slice whose names and types were not yet portable.

### 3.1 Reusable foundations already present

- One `PipelineExecutor` owns ingestion, transforms/tests, metadata, and truthful terminal history.
- `WatermarkStore`, `LeaseStore`, `RunHistoryStore`, and `MetadataStore` already have injected
  contracts and local/BigQuery implementations.
- `WritePattern` captures the logical write modes.
- The canonical metadata spine and `CatalogPublisher` boundary already exist.
- `SecretStoreProvider` and environment/GCP resolution already exist.
- Hosted pipelines are manifest-driven and individually scheduled, identified, and permissioned.
- BigQuery supports bounded SCD1 ingestion, additive top-level schema evolution, load jobs,
  Storage Write staging, run fencing, and replay-safe watermarks.
- The existing Cloud Run Terraform proves immutable images, dedicated identities, scheduling,
  failure alerts, and no-change reconciliation.

### 3.2 Coupling that must be removed deliberately

- `WriteTarget` contains BigQuery `project/dataset/table` vocabulary.
- raw connector schemas use BigQuery type names and modes.
- transform parsing, quoting, materialization, assertions, and metric calculations are BigQuery SQL.
- graph compilation and graph execution are BigQuery-specific.
- failure classification and operator messages name BigQuery, GCP, and Cloud Run.
- `dander.yaml` contains GCP resource-name validation and one global Cloud Run resource profile.
- the CLI constructs BigQuery writers, state stores, transform runners, and Dataplex directly.
- the packaged dependency set installs `dlt[bigquery]` and GCP SDKs unconditionally.
- the Terraform root, backend, bootstrap identity, registry, scheduler, alerts, and verification are
  GCP-specific.
- target DML fencing currently assumes the lease table and destination share BigQuery transaction
  semantics.

### 3.3 Existing work isolation

The uncommitted `codex/oci-runtime-contract-wip` worktree is not a base branch. Its runtime command,
terminal JSON record, launcher run identifier, OCI annotations, and tests are candidate inputs to
the first foundation ticket only after they are rebased and reviewed. No roadmap phase may merge
that worktree wholesale or overwrite other active work.

## 4. Target architecture

### 4.1 Layering

```text
Project YAML / CLI
        |
        v
Validated platform profile
        |
        +--------------------+--------------------+-------------------+
        |                    |                    |                   |
        v                    v                    v                   v
Warehouse adapter      State backend       Catalog publisher    Secret provider
        |                    |                    |                   |
        +--------------------+--------------------+-------------------+
                             |
                             v
                   Cloud-neutral executor
                             |
                             v
                 Versioned OCI runtime contract
                             |
       +-----------+---------+---------+-----------+-----------+
       |           |                   |           |           |
       v           v                   v           v           v
   Cloud Run     Fargate          Kubernetes      Azure        OCI
```

The runtime does not call launcher APIs. Launchers project the same execution specification into
their provider and invoke the runtime. Warehouse, state, catalog, and secret adapters are selected
inside the runtime from the validated profile.

### 4.2 Logical and deployment configuration boundary

Do not add provider conditionals to the logical pipeline manifest. Configuration version 2 splits
portable pipeline intent from environment/deployment projection so the same pipeline can be run
unchanged in shadow profiles.

```yaml
# dander.yaml — logical pipeline contract
version: 2

pipelines:
  salesforce_accounts:
    source: salesforce
    models: [stg_salesforce__accounts]

# dander.platforms.yaml — operator/environment contract
version: 1

platforms:
  production:
    warehouse:
      provider: bigquery
      # provider-specific validated fields
    state:
      provider: bigquery
    catalog:
      provider: dataplex
    secrets:
      provider: gcp_secret_manager

deployments:
  production_cloud_run:
    platform: production
    launcher:
      provider: cloud_run
    pipelines:
      salesforce_accounts:
        schedule: "0 11 * * *"
        time_zone: America/New_York
        paused: true
        secret_bindings: {}
    runtime:
      # cloud-neutral execution settings
```

`dander.yaml` owns sources, endpoints, graphs/models, logical write modes, tests, and metadata.
`dander.platforms.yaml` owns warehouse/state/catalog connections, secret backends, image references,
schedules, identities, secret bindings, resources, networking, logging, and launcher settings. A
runtime invocation selects a platform/deployment explicitly; a pipeline does not bind itself to one
cloud.

Each provider block is a discriminated union. Unknown fields fail validation. Provider modules own
their names, limits, and defaults; the logical model retains only portable semantics.

Version 1 manifests must continue to resolve to an implicit GCP/BigQuery profile until the end of
the deprecation window. `dander config migrate` produces the version 2 logical file plus a GCP
deployment file deterministically and supports `--check` before any write.

### 4.3 Cloud-neutral execution projection

Every launcher consumes one immutable projection with these fields:

- OCI manifest digest and command.
- contract version, pipeline identifier, run identifier, launcher attempt, and shard index/count.
- configuration mount or object reference and profile identifier.
- non-secret environment variables and secret-reference bindings.
- workload identity reference.
- CPU, memory, ephemeral storage, deadline, runtime retry count, and launcher retry count.
- task count, maximum parallelism, schedule, time zone, and pause state.
- network placement and provider-specific extension map.
- labels/tags including Dander version, profile, pipeline, and image digest.
- log destination, metric namespace, alert target, and retention settings.

Unsupported projections fail during plan generation. Launchers must not silently ignore a requested
limit or change a retry count.

### 4.4 Warehouse adapter composition

Do not replace BigQuery classes with one large `WarehouseProvider` interface. Define small
capabilities and compose them in a provider factory:

- `RelationCodec`: validates and renders catalog/namespace/relation identifiers.
- `LogicalTypeMapper`: maps canonical Dander types to provider types and back.
- `SchemaManager`: inspects, creates, and evolves declared relations.
- `RecordWriter`: executes the five logical write modes.
- `BulkLoader`: stages and commits large batches.
- `TransformCompiler`: renders provider SQL from the parsed model representation.
- `TransformRunner`: materializes and tests selected models.
- `TargetFence`: validates the current run token in the same transaction as publication.
- `WarehouseTelemetry`: returns normalized job/query/load statistics.
- `WarehouseCapabilities`: declares supported types, transports, DDL, and limits.

The executor consumes a `WarehouseRuntime` bundle. It never branches on a provider string.

### 4.5 Canonical schema and relations

Replace BigQuery schema vocabulary at shared boundaries with a versioned canonical model covering:

- boolean, signed integer, decimal precision/scale, floating point, string, binary;
- date, time, timestamp with and without timezone;
- JSON/semi-structured data;
- arrays and records where the provider supports them;
- nullable/required cardinality and provider extension metadata.

Adapters must reject lossy mappings unless the manifest explicitly chooses a documented fallback.
Existing BigQuery YAML continues to parse through a compatibility mapper during migration.

Use `RelationRef(catalog, namespace, name)` internally. Provider codecs decide whether the rendered
shape is project/dataset/table, database/schema/table, or database/schema/relation.

### 4.6 Cross-backend fencing and commit protocol

The destination warehouse and state backend may differ, so a distributed transaction is not
assumed.

1. Acquire the pipeline lease and fencing token in the selected state backend.
2. Immediately claim every target-scoped destination fence for the run using the exact state
   authority ID, authority epoch, pipeline, target, run ID, and token.
3. Create a run-scoped staging object in the destination warehouse.
4. Load and validate staged data in bounded batches.
5. In one destination transaction, require exact ownership of the already-claimed destination
   fence, publish the target, and mark the target commit complete.
6. Atomically compare-and-set the watermark in the state backend.
7. Record terminal history and metadata.

Claiming occurs before staging so a newer owner invalidates stale work before either run can
publish. If the process dies between steps 5 and 6, the next run re-reads the old watermark and
safely replays against an idempotent target.

The claim itself is an atomic compare-and-set in the destination: it succeeds only for the
destination's active authority/epoch and a token newer than the recorded owner, or for an exact
idempotent retry of the same run/token. Publication predicates on the full stored tuple, not token
alone. A zero-row conditional update, ownership mismatch, or authority mismatch aborts publication
and is reported as a stale run.

Each state deployment has an immutable authority ID and monotonically increasing authority epoch.
A state-backend cutover must pause schedules, drain or expire leases, claim an exclusive cutover
lock in every affected destination, advance the target authority epoch, migrate/verify state, and
resume. Tokens from an older authority or epoch can never claim or publish.

Each warehouse/write/materialization mode must document and test its atomic publication primitive:

- transactionally fenced DML for keyed writes, snapshots, increments, and stable-table replace;
- object swap/rename only where the provider makes the fence check and swap one atomic operation;
- run-scoped load followed by fenced `DELETE`/`INSERT` for BigQuery stable-table replace;
- provider-specific table/view materialization only after an atomic fence pattern is proven.

BigQuery permanent DDL cannot be described as transactionally fenced merely because ownership was
checked immediately before it. Hosted table/view materialization must use a proven indirection or
stable-relation DML pattern; otherwise that materialization is marked unsupported for concurrent
hosted runs. Every adapter and mode proves stale-token rejection, including a newer lease acquired
before the newer run publishes.

### 4.7 Transform and graph portability

Current model files and graph compilation are BigQuery-specific. Portability requires an explicit
SQL contract rather than assuming arbitrary BigQuery SQL can be translated correctly.

- Add model metadata `dialect: portable|bigquery|snowflake|redshift|postgres`.
- Define a tested portable SQL AST subset for projections, filters, joins, unions, aggregations,
  windows, casts, common scalar functions, and Dander `ref()` relations.
- Parse portable models once with sqlglot, reject unsupported/provider-specific AST nodes, and
  render through the selected warehouse dialect.
- Keep existing SQL files as `bigquery` during compatibility migration; do not silently relabel
  them portable.
- Allow an explicitly named provider variant only when a model needs semantics outside the
  portable subset. Variants share one metadata file, declared output columns, tests, lineage, and
  metric definitions.
- Require a model with provider variants to pass normalized result equivalence against the shared
  conformance fixture.
- Compile graph operations to the same provider-neutral expression/relational AST used by models;
  graph nodes must not construct BigQuery SQL directly.
- Render generic assertions and incremental-materialization control SQL per provider.
- Fail during validation when the selected platform lacks a portable model or an exact provider
  variant.

The portable dialect has canonical semantics, not merely common syntax:

- Ordering must state `NULLS FIRST` or `NULLS LAST`; window ordering must include a declared unique
  tie-breaker whenever peer order can change the result.
- Timestamps cross adapter boundaries as UTC with microsecond precision. Local-time conversion,
  daylight-saving behavior, and higher/lower provider precision require an explicit operation.
- Decimals declare precision, scale, and rounding. Overflow and an inexact provider mapping fail
  instead of falling back to floating point.
- Portable text comparison is case-sensitive with binary/code-point ordering after Unicode NFC
  normalization. Locale collation or case-folding requires a provider variant or explicit portable
  normalization operation.
- Only the documented strict cast matrix is portable. Provider permissive/try-casts and implicit
  string-to-number/date conversion are rejected unless represented by an explicit portable node.
- JSON is compared and hashed by canonical parsed structure; object-key order and original lexical
  formatting are never semantic. Provider-only path, scalar-coercion, or null behavior requires a
  provider variant.
- Unquoted logical identifiers are case-stable canonical names; adapters quote rendered physical
  identifiers. A model cannot depend on a provider folding unquoted names differently.
- Floating-point result assertions use a declared absolute/relative tolerance; exact types remain
  exact.

The compatibility suite contains positive and rejection fixtures for every rule. Normalized output
comparison starts from the same deterministic source rows, sorts by declared stable keys, applies
the canonical timestamp/decimal/JSON representation, and excludes run IDs, timestamps, provider
job IDs, and other intentionally variable metadata.

### 4.8 Dependency packaging

- Python installations expose provider extras: `bigquery`, `snowflake`, `redshift`, `postgres`,
  `gcp`, `aws`, `azure`, and `oci`.
- The official full runtime image contains every first-class runtime adapter so the same manifest
  can run on every launcher.
- Provider SDK imports remain inside provider packages and are lazy until selected.
- The release builds one multi-platform OCI index for supported Linux architectures, subject to
  dependency conformance. Launchers pin the index digest, never a mutable tag.
- The image stays non-root, uses a read-only root filesystem where the launcher supports it, and
  writes only to an explicit temporary/work volume.

### 4.9 Infrastructure organization

- Keep provider stacks separate so selecting one launcher does not initialize credentials or
  provider plugins for every cloud.
- Package versioned roots/modules for `gcp`, `aws`, `azure`, and `oci`; package the Kubernetes
  launcher as a Helm chart.
- Project the shared execution specification into provider inputs, but keep registry, IAM,
  networking, scheduler, logging, alerting, and remote-state bootstrap in provider modules.
- Use provider-native encrypted remote state: GCS for GCP, S3 for AWS, Azure Storage for Azure, and
  OCI Object Storage for OCI. State bootstrap remains an explicit stage-zero operation.
- Preserve existing resource addresses through moves/imports or ship a reviewed migration; never
  replace a live GCP job merely to rename the module.
- Extend deployment verification through one provider protocol that returns normalized checks,
  while each implementation uses real provider read APIs.

## 5. OCI runtime contract

### 5.1 Invocation contract

The runtime entrypoint is stable and launcher-neutral:

```text
dander runtime execute \
  --contract io.dander.runtime/v1 \
  --pipeline PIPELINE_ID \
  --platform PLATFORM_PROFILE \
  --config /workspace/dander.yaml
```

Launchers may provide the following environment values:

- `DANDER_RUN_ID`
- `DANDER_LAUNCHER`
- `DANDER_LAUNCHER_EXECUTION_ID`
- `DANDER_ATTEMPT`
- `DANDER_SHARD_INDEX`
- `DANDER_SHARD_COUNT`
- `DANDER_DEADLINE_AT`
- `DANDER_PRINCIPAL`

The contract specifies validation, maximum lengths, allowed characters, precedence, and how absent
values are generated. Provider identifiers remain opaque strings.

### 5.2 Output contract

- Application events are JSON Lines with contract version, event name, timestamp, run ID,
  pipeline ID, stage, and non-sensitive dimensions.
- One terminal `runtime.completed` record reports `succeeded`, `skipped`, or `failed`, normalized
  aggregates, the stable failure code, and retryability.
- Cursor values, source rows, SQL text containing values, credential material, and unrestricted
  exception text never enter the contract.
- Exit codes distinguish success, invalid invocation/configuration, retryable failure, permanent
  failure, and graceful cancellation.
- SIGTERM starts bounded graceful cancellation and emits a terminal record if time remains.
- SIGKILL cannot emit a terminal record; lease expiry and the next successful acquisition reconcile
  the interrupted run.

### 5.3 Artifact contract

- OCI annotations identify source, revision, version, license, documentation, and created time.
- The release records the OCI index digest and per-platform manifest digests.
- The image includes the project runtime, connector/plugin pins, SQL models, graph files, and a
  machine-readable capability manifest.
- `dander runtime inspect` reports contract, build, adapter, and plugin metadata without connecting
  to providers.
- A generated SBOM and dependency lock accompany the release artifact.
- A local conformance command mounts an example project, runs a credential-free pipeline, and
  validates stdout, stderr, exit status, filesystem writes, and signal behavior.
- Build the multi-platform OCI index once in the release pipeline. Promote it to GAR, ECR, ACR, and
  OCIR using registry-to-registry OCI copy, never a provider-specific rebuild.
- Resolve and compare the copied index digest and every per-platform manifest digest before a
  launcher may deploy it. A registry that rewrites the manifest is a failed promotion, not “the
  same image.”

## 6. Provider implementation plan

### 6.1 BigQuery warehouse

Refactor the existing implementation behind the new capability bundle without changing behavior
before adding new functionality.

Required parity and scale work:

- Characterize current SQL, DDL, schema, load, fencing, and failure behavior before moves.
- Move provider types and retry policy into `dander.providers.bigquery`.
- Map canonical schema to BigQuery scalar, repeated, and record fields.
- Retain load jobs for general batch loads and Storage Write pending streams where selected.
- Stream all write modes; remove remaining full-logical-batch buffering from SCD2, snapshot,
  incremental, and Storage Write orchestration.
- Add GCS/Parquet staging for loads above the measured direct-upload crossover point.
- Preserve run-scoped staging expiration and deterministic cleanup.
- Make partitioning, clustering, schema evolution, and query labels provider configuration.
- Capture bytes processed/billed, slot milliseconds, load bytes, affected rows, and job IDs.
- Support statement maximum-bytes and timeout controls.
- Prove native GCP service-account auth and keyless AWS/Azure workload federation to BigQuery.

### 6.2 PostgreSQL warehouse

- Support PostgreSQL 15+ as the first relational reference implementation.
- Use TLS-required connections and a bounded connection pool.
- Use `COPY FROM STDIN` into run-scoped staging for bulk ingestion.
- Use transactional publication with `INSERT ... ON CONFLICT`, `MERGE`, or staged
  delete/insert according to write mode and server capability.
- Implement replace by loading a new relation and performing the smallest safe transactional swap
  supported by dependencies and constraints.
- Map canonical types, including JSONB and arrays where declared.
- Implement additive columns; reject unsafe type, nullability, and destructive changes.
- Render PostgreSQL identifiers and SQL through the provider dialect.
- Add statement, lock, and idle-transaction timeouts.
- Expose optional partition definitions and required unique indexes for keyed writes.
- Record copy bytes/rows, transaction duration, lock wait, affected rows, pool wait, and retries.
- Test against vanilla PostgreSQL plus one managed representative when credentials are available.

### 6.3 Snowflake warehouse

- Use the supported Snowflake Python connector with key-pair or OAuth authentication; secrets are
  references, never connection literals in YAML.
- Use compressed Parquet files and `COPY INTO` through a run-scoped internal or external stage for
  bulk loads.
- Use bounded direct writes only below a measured crossover threshold.
- Track staged file checksums and load history so retries cannot duplicate a run.
- Publish from transient staging tables with provider `MERGE` and transactional fence checks.
- Implement the five write modes and canonical schema mapping, including `VARIANT` fallbacks only
  when explicitly selected.
- Render Snowflake SQL, identifiers, assertions, tables, views, and incremental models.
- Apply query tags containing Dander run and pipeline identifiers.
- Configure statement and queued timeouts.
- Expose warehouse name/size while treating auto-resume, auto-suspend, resource monitors, and
  multi-cluster scaling as verified infrastructure settings rather than changing them per run.
- Capture query ID, warehouse, credits/compute time where available, bytes scanned/written, rows,
  queue time, spill, and retry metrics.

### 6.4 Redshift warehouse

- Support provisioned RA3 and Redshift Serverless through one SQL/data-plane adapter with distinct
  infrastructure profiles.
- Stage compressed Parquet files in a run-scoped S3 prefix and load with parallel `COPY` plus a
  manifest.
- Use IAM roles for Redshift-to-S3 access; never place AWS access keys in SQL.
- Validate load files before publication and retain normalized load-error summaries.
- Publish through staging tables and Redshift `MERGE`, including target-fence validation.
- Implement the five write modes and canonical schema mapping, including SUPER only when selected.
- Support automatic distribution/sort defaults and explicit validated overrides.
- Render Redshift SQL, identifiers, assertions, tables, views, and incremental models.
- Set query group/run labels for WLM and observability.
- Treat automatic analyze/optimization as the default; request explicit analyze only when measured
  table change and workload evidence require it.
- Capture query/load IDs, queue time, execution time, bytes, rows, spill, WLM queue, and retries.

## 7. State backend plan

### 7.1 Shared state schema

Version and migrate these logical tables:

- `dander_schema_migrations`
- `dander_pipeline_leases`
- `dander_watermarks`
- `dander_runs`
- `dander_metadata_snapshots`
- `dander_target_commits` in each destination warehouse

Use one lease table keyed by pipeline rather than one physical BigQuery table per pipeline once
parity and query-cost effects have been measured. Migrations must be additive and recoverable; a
release refuses to run against a newer unknown schema version.

### 7.2 BigQuery state

- Port existing behavior into the shared schema and conformance suite.
- Preserve server-time lease expiry, monotonically increasing fencing tokens, CAS watermarks,
  sanitized history, and interrupted-run reconciliation.
- Parameterize every value and label state jobs with run identifiers.
- Measure query count and minimum billing effects; coalesce safe checkpoints where it reduces cost
  without weakening lifecycle truth.

### 7.3 PostgreSQL state

- Use short transactions and a dedicated bounded pool separate from warehouse query connections.
- Acquire expired leases using row locking and server time; increment the fence in the same
  transaction.
- Heartbeat and release only the exact pipeline/run/token tuple.
- CAS watermarks by expected value and reject stale writers.
- Store JSON metadata snapshots in JSONB with deterministic serialized content.
- Index pipeline/status/start time and source/entity lookups.
- Add retention configuration for terminal history while preserving active/interrupted records.
- Prove behavior during connection loss, database restart, clock differences, lock contention, and
  pool exhaustion.

## 8. Catalog and secret plan

### 8.1 Canonical catalog changes

- Remove BigQuery-specific `project/dataset` assumptions from `CatalogAsset`.
- Store canonical relation coordinates, provider, logical types, lineage, tests, metrics,
  ownership, and sensitivity.
- Let each warehouse dialect render human calculations while the canonical metric retains a typed
  aggregation and field reference.
- Keep the local semantic registry byte-stable across launchers and cloud catalogs.

### 8.2 Dataplex

- Preserve current aspect-only updates for first-party BigQuery entries.
- Add custom-entry projection for Snowflake, Redshift, and PostgreSQL assets where a first-party
  Dataplex entry is unavailable.
- Normalize generated aspects and custom-entry identifiers for read-back comparison.
- Keep publication explicit and independently retryable after data succeeds.

### 8.3 AWS Glue Data Catalog

- Publish canonical databases/tables directly through the Glue API; do not make crawlers the
  source of truth for Dander-authored metadata.
- Map columns, relation location/connection, classification, lineage references, ownership,
  sensitivity, tests, metrics, and Dander manifest checksum into owned parameters/tags.
- Support Redshift, PostgreSQL, Snowflake, and BigQuery representations without requiring the
  warehouse and catalog to share a cloud.
- Read back and normalize the published table.
- Preserve unrelated table parameters and never delete tables by default.

### 8.4 No catalog

- Compile and store the canonical snapshot.
- Skip all catalog credentials, IAM, network calls, infrastructure, and alerts.
- Report `catalog.provider=none` and zero published assets without treating the stage as absent or
  failed.

### 8.5 Secret resolution

- Adopt explicit reference schemes: `env://`, `gcp-sm://`, `aws-sm://`, `azure-kv://`, and
  `oci-vault://`.
- Preserve existing environment-name and GCP resource-name forms through compatibility parsing.
- Add AWS Secrets Manager runtime resolution with ambient task/pod identity and audited access.
- Prefer launcher-native projection for Cloud Run, ECS, and Azure Container Apps when the platform
  can inject a managed secret without exposing it to Terraform state.
- Add the smallest OCI Vault runtime resolver needed because Container Instances do not provide a
  complete arbitrary application-secret projection equivalent.
- Keep direct environment injection for local development and externally managed Kubernetes.
- Audit provider, reference, principal, pipeline, and time; never audit the value.
- Test rotation during retries and token/credential refresh without rebuilding the image.

### 8.6 Workload identity and credential support matrix

Identity portability is an explicit allowlist. A combination absent from the released matrix is
unsupported and fails planning. `experimental` combinations require `--allow-experimental` and
print the missing proof; `supported` requires a live bootstrap, token exchange, refresh, rotation,
and revocation test. No supported or experimental hosted profile may use a service-account key,
AWS access-key pair, client secret standing in for workload identity, or another long-lived cloud
credential.

| Launcher principal | Target services | Credential flow | Release status after proof |
|---|---|---|---|
| Cloud Run service account | BigQuery, Dataplex, GCP Secret Manager | GCP Application Default Credentials from the attached service account | supported |
| ECS task role | Redshift/S3, Glue, AWS Secrets Manager | ECS task-role credentials; the task execution role is separate and limited to image pull/log/declared secret projection | supported |
| ECS task role | BigQuery/Dataplex/GCP Secret Manager | AWS signed workload identity exchanged through Google Workload Identity Federation; Google token refresh occurs inside the running task | supported |
| GKE/EKS/AKS/OKE service account | Same-cloud APIs | The cluster's documented workload-identity binding; each cluster type is a separate tested profile | supported only per proven cluster profile |
| Kubernetes service account | Snowflake/PostgreSQL | TLS plus external-secret/operator-projected OAuth, key-pair, or database credential | supported in the named Kubernetes profile |
| Azure user-assigned managed identity | ACR, Key Vault, Azure logging | Azure managed identity | experimental after Phase 6; support requires Phase 8 qualification |
| Azure user-assigned managed identity | BigQuery/Dataplex/GCP Secret Manager | Azure workload identity exchanged through Google Workload Identity Federation, including refresh | experimental after Phase 6; support requires Phase 8 qualification |
| Azure job | Snowflake/PostgreSQL | Key Vault-projected OAuth, key-pair, or database credential over TLS | experimental in the named Azure profile until Phase 8 qualification |
| OCI Container Instance resource principal | OCIR, Vault, Logging/Monitoring | OCI resource principal and dynamic-group policy | supported |
| OCI job | Snowflake/PostgreSQL | OCI Vault-resolved OAuth, key-pair, or database credential over TLS | supported in the named OCI profile |
| OCI resource principal | BigQuery/Dataplex/GCP Secret Manager | External workload exchange still to be proven with refresh | experimental until Phase 7 proof |
| Any non-AWS principal | Redshift/S3, Glue, AWS Secrets Manager | Short-lived AWS federation is not yet defined or proven | unsupported until a dedicated identity profile passes live proof |

Environment secrets are supported for local execution and operator-managed Kubernetes only. They
are not a fallback that turns a failed hosted identity flow into a supported profile. Every live
identity test records the non-secret issuer, subject, audience, principal, credential expiry, and
refresh event so the proof distinguishes initial login from sustained execution.

## 9. Launcher implementation plan

### 9.1 Cloud Run Jobs

- Refactor the current `scheduled-job` module into a GCP launcher consuming the shared projection.
- Preserve Artifact Registry, dedicated runtime/scheduler identities, Secret Manager references,
  Cloud Scheduler, Cloud Logging, Monitoring alerts, immutable digest validation, and paused-first
  deployment.
- Add explicit task count/parallelism, execution attempt/run ID injection, ephemeral storage,
  cancellation, and normalized execution status.
- Keep task parallelism at one until a pipeline declares a deterministic shard strategy.
- Verify Cloud Run limits during plan generation.
- Prove behavior parity before deleting or moving existing Terraform addresses.

### 9.2 ECS/Fargate

- Provision ECR, ECS cluster, task definitions, execution role, per-pipeline task role, log group,
  EventBridge Scheduler schedule/role, a Step Functions execution controller, failure-event rule,
  notification/DLQ target, and required VPC inputs.
- Use Fargate platform settings with explicit CPU, memory, ephemeral storage, architecture,
  deadline, stop timeout, and read-only filesystem where supported.
- Inject Secrets Manager references through the task definition and use the task role for runtime
  AWS access.
- Support manual `RunTask`, scheduled invocation, status, logs, cancellation, and replay through
  Dander CLI commands.
- Correlate task ARN, scheduler attempt, container exit code, and terminal runtime event.
- Support Fargate Spot only as an explicit interruption-tolerant profile, never the default.
- First live proof: the same OCI digest as Cloud Run executes the same BigQuery pipeline using
  keyless Google federation and normalized output comparison.

EventBridge delivery and Dander execution attempts are separate counters. The scheduler starts one
idempotently named state-machine execution with the pipeline, scheduled occurrence, and deployment
revision. The controller:

1. derives the Dander run/idempotency key and returns the existing execution on duplicate delivery;
2. calls `RunTask`, records the task ARN, and polls `DescribeTasks` on a bounded interval;
3. compares provider time to the absolute execution deadline and calls `StopTask` when exceeded;
4. classifies stopped/essential-container state, exit code, and terminal runtime event;
5. starts a new task only for a retryable outcome and while the explicit launcher-attempt limit and
   overall deadline both remain; and
6. writes the normalized terminal execution record and sends exhausted controller failures to the
   DLQ/alert path.

Control-plane API retries do not increment the launcher attempt. A runtime self-deadline and ECS
stop timeout assist graceful shutdown but are not the sole hard-deadline mechanism.

### 9.3 Kubernetes and Helm

- Publish one versioned Helm chart that targets an existing conforming Kubernetes cluster; cluster
  creation is outside the chart.
- Render ConfigMap, ServiceAccount, Job template, CronJob, RBAC, optional secret references,
  resources, deadlines, retries, concurrency policy, job-history limits, and cleanup TTL.
- Default CronJob concurrency policy to `Forbid`; Dander leases remain the final overlap defense.
- Use `restartPolicy: Never`, bounded `backoffLimit`, and explicit requests/limits.
- Support indexed jobs only after a pipeline declares stable shards.
- Allow workload-identity annotations and pod labels without embedding one cloud’s identity model in
  the chart.
- Integrate with cluster logging/metrics through stdout and standard annotations; do not require a
  bundled observability stack.
- Test chart rendering, upgrade, rollback, uninstall retention policy, manual Job creation, CronJob
  scheduling, SIGTERM, eviction, duplicate start, and cleanup.

### 9.4 Azure Container Apps Jobs

- Provision resource group inputs, ACR, Container Apps environment, scheduled/manual job,
  user-assigned managed identity, Key Vault bindings, logging workspace, alerts, and network inputs.
- Map projection fields to replica timeout, retry limit, parallelism, completion count, CPU, memory,
  schedule, environment, secrets, and managed identity.
- Use ACR and Key Vault without username/password material in the project manifest.
- Support manual start, schedule pause/resume, execution history, logs, cancellation where the API
  permits it, and replay through Dander CLI commands.
- Prove keyless Azure-to-BigQuery access before claiming the BigQuery profile supported.
- Run the same digest and comparison workload used by Cloud Run and Fargate.

### 9.5 Oracle Cloud Infrastructure

- Use OCI Container Instances as the native non-Kubernetes runtime, OCIR as the registry, resource
  principals/dynamic groups for identity, OCI Vault for application secrets, OCI Logging/Monitoring
  for telemetry, and Events for lifecycle reactions.
- Configure `Never` or `OnFailure` restart policy according to the projection, explicit flexible
  shape, resource limits, VCN/subnet, graceful shutdown, and immutable image digest.
- Add a narrow OCI Function invoked as a detached call by OCI Resource Scheduler, and manually by
  the CLI, to start or create the required Container Instance execution because OCI has no direct
  scheduled-container-job primitive equivalent to Cloud Run Jobs or Container Apps Jobs.
- Persist the OCI execution correlation identifier outside the container so status remains
  available when the instance becomes inactive.
- Handle create/start, active, inactive, failed, stop, and delete lifecycle states idempotently.
- Support parallel shards by creating distinct run-scoped instances only after the shard contract
  is proven.
- Prove OCI ambient identity for OCI services and complete an early feasibility gate for keyless
  access to an external warehouse. If OCI-to-Google federation cannot satisfy refresh and workload
  identity requirements, the BigQuery-on-OCI profile remains experimental while native PostgreSQL
  or Snowflake profiles proceed.
- Keep OKE support under the Kubernetes/Helm launcher; do not duplicate it inside the OCI launcher.

The Resource Scheduler body contains only pipeline ID, deployment revision, scheduled occurrence,
and schedule ID. Its OCID is placed in a dynamic group with permission to invoke the launch
Function; the Function's resource principal receives only the Container Instance, Vault reference,
tag, log, and network permissions it uses. The Function derives an idempotency key, searches the
execution-record store/tags before creating anything, and persists run ID, attempt, deadline,
Container Instance OCID, image digest, and lifecycle state.

A separately scheduled reconciliation Function, also triggered early by OCI lifecycle Events,
observes active executions. It stops an instance past its absolute deadline, classifies the runtime
terminal event/container state, starts a fresh run-scoped instance only for an allowed retryable
attempt, and marks exhausted attempts terminal. Duplicate launch, Event, and reconciliation calls
are idempotent. Pause/resume disables/enables the Resource Scheduler schedule; it does not delete
instances or execution history.

## 10. Efficiency and scale program

### 10.1 Shared runtime controls

- Stream source records through bounded queues; memory must be proportional to configured batch
  size and schema, not total endpoint size.
- Separate extraction concurrency, transformation concurrency, load concurrency, pipeline
  concurrency, and launcher shard parallelism.
- Apply backpressure from the selected writer to extraction.
- Classify retryable errors and use bounded exponential backoff with provider `Retry-After` where
  supplied.
- Make batch rows, batch bytes, staged file size, in-flight batches, and worker count independently
  configurable within adapter limits.
- Add adaptive batching only after static profiles are benchmarked; preserve deterministic minimum
  and maximum bounds.
- Use columnar compressed staging for large warehouse loads.
- Keep run-scoped staging names, expiration, checksums, and cleanup.
- Reuse provider clients and connection pools within a run; never share unsafe sessions across
  processes.
- Push transformations into the selected warehouse rather than moving transformed rows through the
  runtime.
- Attach run/pipeline labels or query tags to every provider job and statement.
- Expose a normalized `RunPerformance` record with rows, bytes, duration, throughput, peak RSS,
  retries, queue time, load time, transform time, catalog time, and provider cost dimensions.

### 10.2 Provider-specific efficiency controls

| Provider | Required scale path | Required controls |
|---|---|---|
| BigQuery | GCS/load jobs or Storage Write pending streams | batch bytes/rows, file sizing, partition/clustering, max bytes billed, job labels, slot/load metrics |
| Snowflake | compressed stage files plus `COPY INTO` | file sizing/count, warehouse size, auto-suspend/resume verification, timeout/query tag, merge staging, queue/spill metrics |
| Redshift | parallel S3 Parquet files plus manifest `COPY` | file distribution, WLM/query group, Serverless/RA3 profile, sort/distribution strategy, analyze evidence, queue/spill metrics |
| PostgreSQL | `COPY FROM STDIN` plus transactional staging | pool size, COPY chunk bytes, statement/lock timeout, indexes, partitioning, autovacuum evidence, lock/pool metrics |
| Cloud Run | task count and bounded parallelism | CPU/memory/ephemeral storage, timeout/retries, task index, quota validation, execution metrics |
| Fargate | one task per pipeline or stable shard | task sizing, ephemeral storage, Spot opt-in, VPC placement, scheduler/DLQ retries, task metrics |
| Kubernetes | Job/CronJob, optionally indexed | requests/limits, parallelism/completions, backoff, deadline, concurrency policy, TTL/history, eviction metrics |
| Azure | Container Apps Job replicas | CPU/memory, replica timeout/retries, parallelism/completions, schedule, execution/log metrics |
| OCI | Container Instance per run or shard | flexible shape, restart policy, graceful shutdown, VCN placement, lifecycle work requests/events, instance/container metrics |

Deadline and retry controls must be mechanically enforced, not merely copied into environment
variables:

| Launcher | Hard deadline | Retry owner |
|---|---|---|
| Cloud Run | Job task timeout/cancellation plus runtime graceful deadline | Cloud Run task retry count mapped exactly from the projection |
| ECS/Fargate | Step Functions controller observes absolute deadline and calls `StopTask` | Controller starts a new task for each bounded launcher attempt |
| Kubernetes | `activeDeadlineSeconds` and Job deletion/cancellation | Job `backoffLimit`, with pod restarts disabled |
| Azure Container Apps Jobs | Replica timeout and execution stop/cancel reconciliation | Job replica retry limit mapped exactly from the projection |
| OCI Container Instances | Reconciliation Function observes absolute deadline and stops the instance | Reconciler creates a new run-scoped instance for each bounded launcher attempt |

The runtime owns only in-process transient retries such as an HTTP page retry or warehouse polling
retry. Launcher attempts rerun the whole process and therefore always pass through lease/fence and
idempotency checks.

### 10.3 Scale test profiles

Define the SLO document before optimization. Each profile records input rows/bytes, row width,
schema depth, source rate limit, transform complexity, concurrency, allowed latency, peak memory,
and cost ceiling.

Required benchmark classes:

- correctness: small deterministic fixture with byte-for-byte normalized output comparison;
- bounded-memory: input materially larger than container memory;
- bulk throughput: wide and narrow tables using each provider bulk path;
- incremental: small deltas against a large existing target;
- concurrent pipelines: independent targets plus controlled contention on one target;
- transform: scan, join, aggregation, incremental merge, and generic tests;
- failure: source throttling, dropped connection, credential expiry, state outage, catalog outage,
  process termination, launcher retry, and warehouse cancellation.

No universal “efficient at scale” claim is made from local mocks. Large paid benchmarks run only
with an explicit budget and approval. Results include provider region, service tier/shape, dataset
shape, configuration, image digest, date, and raw provider job identifiers.

### 10.4 Optimization gate

An optimization ships only when it:

1. improves an agreed SLO or cost metric in a reproducible benchmark;
2. retains adapter conformance and normalized result equality;
3. keeps retries and interruption idempotent;
4. does not make the default unsafe or materially more expensive; and
5. records the before/after evidence and provider configuration.

### 10.5 Cost and capacity controls

- Preserve the existing GCP budget preflight as a GCP-only control; remove it from the portable
  runtime path unless the selected profile enables it.
- Add provider verification for AWS budgets/alerts, Azure Cost Management budgets, OCI budgets,
  and Snowflake resource monitors where the operator chooses those controls.
- Validate service quotas and requested launcher parallelism before enabling a schedule.
- Attribute warehouse queries, staged objects, container executions, and state operations to the
  Dander run/pipeline tags supported by each provider.
- Report estimated and observed provider cost dimensions with benchmark evidence; never describe a
  budget alert or delayed billing notification as a hard spending cap.
- Require explicit approval for capacity reservations, multi-cluster Snowflake settings, Redshift
  concurrency scaling, paid Kubernetes nodes, or any live benchmark expected to incur material
  cost.

## 11. Verification strategy

### 11.1 Test layers

| Layer | Scope | Required execution |
|---|---|---|
| Characterization | Existing GCP/BigQuery behavior | Before refactoring each boundary |
| Unit | Canonical models, dialects, factories, validation, retry classification | Every PR |
| Adapter conformance | Warehouse, state, catalog, secret, launcher contracts | Every provider PR |
| Local integration | OCI runtime, PostgreSQL, Helm rendering, failure injection | Every PR where practical |
| Infrastructure validation | Terraform format/validate/tests and Helm lint/template | Every infrastructure PR |
| Live provider smoke | Provision, run, status, logs, rollback, no-change | Provider-gated CI/manual workflow |
| Cross-provider proof | Same digest/config/data with normalized result comparison | Each milestone gate |
| Scale | SLO and cost evidence | Release candidate workflow with approval |

### 11.2 Avoiding a false Cartesian promise

Five launchers, four warehouses, two state backends, three catalog modes, and multiple secret
sources create more than one hundred combinations. Dander will not claim every Cartesian
combination without evidence.

Use three controls:

- each individual adapter passes its full conformance suite;
- a pairwise integration matrix exercises every provider and every cross-boundary interaction at
  least once;
- named production profiles receive end-to-end live proof and an explicit support status.

The CLI publishes the tested compatibility matrix for the installed version and fails early for a
known-unsupported combination.

### 11.3 Canonical live profiles

| Profile | Launcher | Warehouse | State | Catalog | Secret path |
|---|---|---|---|---|---|
| GCP native | Cloud Run | BigQuery | BigQuery | Dataplex | GCP Secret Manager |
| AWS native | ECS/Fargate | Redshift | PostgreSQL | Glue | AWS Secrets Manager |
| Kubernetes portable | Kubernetes/Helm | PostgreSQL | PostgreSQL | none | environment/external secret projection |
| Snowflake multicloud | Azure Container Apps Jobs | Snowflake | PostgreSQL | none | Azure Key Vault projection |
| OCI native runtime | OCI Container Instances | PostgreSQL | PostgreSQL | none | OCI Vault |

Additional portability proofs run the BigQuery profile from Fargate and Azure using keyless
federation and from Kubernetes using the cluster’s supported workload identity. OCI-to-BigQuery is
gated separately because its external identity path is not equivalent to the documented AWS/Azure
paths.

The Azure canonical profile uses catalog `none`; Glue is proven in the AWS canonical profile and
may be added to Azure only after a separate short-lived AWS identity proof.

### 11.4 Required failure assertions

- Duplicate launcher delivery never duplicates a target business key.
- Overlapping schedules produce one owner and one truthful skipped run.
- A stale fencing token cannot publish after a newer token.
- SIGTERM either commits a complete boundary or leaves replay-safe staging.
- SIGKILL leaves no successful terminal event and is reconciled after lease expiry.
- A crash after target commit but before watermark CAS replays safely.
- Secret rotation is observed without image rebuild or secret logging.
- Catalog failure does not misreport a data write as fully successful.
- Provider throttling exhausts bounded retries with a stable retryability code.
- Schema drift fails before destructive target mutation.
- Upgrade and rollback preserve state-schema compatibility.

### 11.5 Initial tri-state compatibility matrix

This is the target release matrix, not a claim about the current code. A row moves to `supported`
only after all referenced adapter, identity, launcher, and live-profile proofs pass in the same
release candidate.

| Combination | Target status | Required evidence |
|---|---|---|
| Cloud Run + BigQuery state/warehouse + Dataplex + GCP secrets | supported | GCP canonical profile and regression parity |
| Fargate + BigQuery state/warehouse + Dataplex + GCP secrets | supported | identical-digest AWS-to-Google federation and refresh proof |
| Fargate + PostgreSQL state + Redshift + Glue + AWS secrets | supported | AWS canonical profile |
| Kubernetes/Helm + PostgreSQL state/warehouse + no catalog + external secrets | supported | one named cluster profile plus portable chart conformance |
| Azure Container Apps Jobs + PostgreSQL state + Snowflake + no catalog + Key Vault | experimental after Phase 6; supported target after Phase 8 | Azure canonical profile plus Phase 8 qualification |
| Azure Container Apps Jobs + BigQuery state/warehouse + Dataplex + GCP secrets | experimental after Phase 6; supported target after Phase 8 | Azure-to-Google federation/refresh proof plus Phase 8 qualification |
| OCI Container Instances + PostgreSQL state/warehouse + no catalog + OCI Vault | supported | OCI canonical profile |
| OCI Container Instances + BigQuery/Dataplex/GCP secrets | experimental | OCI external-identity feasibility, refresh, and revocation proof |
| Non-AWS launcher + Redshift, Glue, or AWS Secrets Manager | unsupported | remains so until an explicit short-lived AWS federation profile is designed and proven |
| Any unlisted combination | unsupported | explicit matrix addition, pairwise coverage, identity proof, and live smoke required |

Warehouse/catalog combinations that use ordinary TLS credentials may be added without redesigning
the runtime, but they still begin as unsupported. Adapter conformance alone does not promote an
end-to-end combination.

## 12. Delivery sequence and gates

### Completed-gate preservation

Beginning a later phase never waives an earlier exit gate. Each previously completed gate must
remain satisfied on the current protected-main commit or have current equivalent evidence. A
regression in an earlier gate blocks later-phase implementation until the gate is restored.

| Gate | Current equivalent evidence |
|---|---|
| Phase 0 | `cloud-portability-baseline.md` plus protected-main CI |
| Phase 1 | `cloud-portability-phase1-acceptance.md` and the versioned OCI runtime/projection tests |
| Phase 1B | `cloud-portability-phase1b-acceptance.md` and retained artifact/identity conformance |
| Phase 2 | Version 2 platform profiles, typed lazy factories, canonical schema/relation contracts, and migration parity tests |
| Phase 3 | `cloud-portability-fargate-lifecycle-acceptance.md` plus current GCP compatibility and no-drift evidence |
| Phase 4 | `cloud-portability-postgresql-kubernetes-acceptance.md`, state/warehouse conformance, and the published compatibility matrix |

The foundation and BigQuery portability proof are serial gates. After Phase 3, bounded provider
lanes may proceed concurrently, but no lane skips its shared-contract dependency or integration
gate.

```text
Baseline
   |
OCI contract + execution projection
   |
Artifact-copy + Fargate identity feasibility gate
   |
Platform profiles + canonical schema/SQL/state contracts
   |
BigQuery + Cloud Run + ECS/Fargate portability proof
   |
   +----------------------------------+
                       |
        +--------------+---------------+----------------+
        |                              |                |
PostgreSQL state/warehouse       Snowflake/Redshift   Kubernetes
        |                              |                |
        +----------------------+-------+----------------+
                               |
                        Glue catalog proof
                               |
                     +---------+---------+
                     |                   |
                  Azure                OCI
                     |                   |
                     +---------+---------+
                               |
                   Pairwise matrix + scale qualification
```

### Phase 0 — Baseline and branch safety

Deliverables:

- Merge or otherwise resolve all active release and synchronization work before implementation.
- Create the implementation branch/worktree from the then-current `origin/main`.
- Inventory the OCI-runtime WIP and salvage only reviewed commits.
- Record the explicit product-direction change from GCP-native to cloud-selectable, update the
  binding steering scope, and retain GCP/BigQuery as the primary compatibility profile.
- Add characterization tests around current BigQuery SQL, state transitions, Cloud Run projection,
  CLI behavior, and distribution contents.
- Approve this target matrix and the initial SLO document.

Exit gate:

- clean base, no unrelated worktree changes, current full suite green, and no behavior changes.

### Phase 1 — OCI runtime contract and execution projection

Tickets:

1. versioned invocation, event, terminal-result, exit-code, and signal contract;
2. `dander runtime inspect` and local OCI conformance command;
3. OCI annotations, digest recording, SBOM, non-root/read-only filesystem tests;
4. cloud-neutral execution projection and capability/limit validation;
5. Cloud Run consumes the projection with exact behavior parity.

Exit gate:

- the same digest runs locally and on Cloud Run; normalized outcomes match; SIGTERM/SIGKILL and
  replay behavior are proven; existing Cloud Run deployment reconciles with no unplanned changes.

### Phase 1B — Artifact-copy and cross-cloud identity feasibility gate

This deliberately precedes the broad provider refactor. It is a narrow live proof that the central
artifact and identity assumptions work in a real AWS task.

Tickets:

1. build the multi-platform runtime image once and publish the release candidate to staging GAR;
2. copy the OCI index from GAR to ECR without rebuilding and verify the index and per-platform
   digests at both registries;
3. create an isolated minimal Fargate smoke stack with a task execution role for ECR/logging and a
   separate task role for workload access;
4. exchange the task role identity through Google Workload Identity Federation without a Google
   service-account key or static AWS access key;
5. run a BigQuery read/query, keep the probe alive long enough to observe the issued Google
   credential expire and refresh, then run a second query; and
6. scan configuration, Terraform state, task definition, logs, and image contents for prohibited
   long-lived cloud credentials and tear down or leave the paid schedule disabled.

Exit gate:

- Cloud Run and Fargate pull byte-identical per-platform content; Fargate reaches BigQuery before
  and after credential refresh; task/execution roles are correctly separated; no static cloud key
  is present. Failure stops the cross-cloud profile work before factories and adapters are widened.

### Phase 2 — Platform profiles and factories

Tickets:

1. version 2 named platform profiles and discriminated provider configuration;
2. deterministic version 1 migration and compatibility window;
3. provider registry/factories for warehouse, state, catalog, secrets, and launchers;
4. canonical relation and schema model;
5. portable transform/graph AST, explicit dialect metadata, and BigQuery compatibility marking;
6. generic failure codes, logs, and run telemetry;
7. optional provider extras plus full runtime-image dependency assembly.

Exit gate:

- the current GCP project migrates with byte-equivalent logical behavior, and provider SDKs are not
  imported when their adapter is unselected.

### Phase 3 — Portable BigQuery vertical slice

Tickets:

1. move BigQuery writer/transform/schema/fence/telemetry behind provider capabilities;
2. move BigQuery state behind the shared state schema and migrations;
3. route BigQuery model and graph execution through the dialect/capability boundary with parity;
4. retain Dataplex and GCP secret behavior through provider factories;
5. implement ECS/Fargate launcher, ECR publication, AWS secret path, and AWS-to-Google federation;
6. deterministic local/Cloud Run/Fargate comparison workflow;
7. interruption, overlap, credential refresh, rollback, and no-change proofs.

Exit gate:

- one logical BigQuery pipeline and one OCI digest pass locally, Cloud Run, and Fargate with equal
  normalized results and keyless cloud identity. This is the first cloud-portable release gate.

### Phase 4 — PostgreSQL state and warehouse

Tickets:

1. PostgreSQL migrations and full state-conformance implementation;
2. destination-side generic target-fence protocol in BigQuery and PostgreSQL;
3. PostgreSQL schema, writer, bulk loader, transform, assertions, and telemetry;
4. Kubernetes/Helm launcher with existing-cluster deployment verification;
5. PostgreSQL native profile and cross-backend BigQuery/PostgreSQL matrix;
6. bounded-memory and concurrent-run benchmarks.

Exit gate:

- Kubernetes profile runs PostgreSQL warehouse/state end to end; both state backends pass the same
  fault tests; BigQuery behavior remains unchanged.

### Phase 5 — Snowflake and Redshift

Tickets:

1. shared compressed-columnar staging artifact writer;
2. Snowflake stage/COPY, schema, writes, transforms, fence, telemetry, and live profile;
3. Redshift S3/COPY, schema, writes, transforms, fence, telemetry, and live profile;
4. AWS Glue publisher and normalized read-back;
5. warehouse capability matrix and unsupported-type diagnostics;
6. shared deterministic four-warehouse correctness fixture and normalized result comparison.

Provider-specific throughput, incremental-at-scale, transform, concurrency, crossover, cost,
soak, pairwise-profile, and release qualification are Phase 8 work. Reclassifying those checks does
not weaken them or permit a scale, efficiency, cost, reliability, or support claim before they
pass.

Exit gate:

Before Phase 6 implementation begins:

1. Protected `main` is clean at a recorded commit; required CI is green; all Phase 5 implementation
   and evidence PRs are merged; and the roadmap, relevant tickets, compatibility matrix, known
   limitations, and provider documentation agree with that commit.
2. Every previously completed phase exit gate remains satisfied or has current equivalent evidence.
3. BigQuery, PostgreSQL, Snowflake, and Redshift are selectable through the same canonical relation,
   schema, writer, transform, fencing, telemetry, and provider-factory contracts without provider
   branching in the executor.
4. Every capability advertised by each warehouse passes deterministic unit and adapter-conformance
   tests, including schema behavior, bounded ingestion, every advertised write mode and transport,
   replay/idempotency, stale-fence rejection, supported materializations, assertions, graphs,
   cleanup, telemetry availability, and fail-before-mutation diagnostics.
5. One shared deterministic fixture runs against all four warehouses. Results are normalized and
   equal for the common canonical semantic intersection. Provider-specific types, fallbacks,
   transports, and materializations are tested separately and are not treated as equal merely
   because an adapter exists.
6. Known materialization, transport, schema, and fencing limitations fail closed before mutation
   and are explicit in the compatibility matrix and known limitations. No second machine-readable
   capability-schema revision is required before Phase 6 unless Phase 6 behavior consumes that
   granularity.
7. Snowflake and at least one Redshift deployment profile have current live warehouse-correctness
   evidence with credentials excluded and exact cleanup verified.

Provider-specific limitations may remain experimental or unsupported when they are explicit,
safely rejected, and not required by the named Phase 6 profile. They do not become support claims.

Before a provider or named profile is promoted to supported, every advertised capability must pass
its full conformance suite and the applicable live identity, schedule, duplicate-delivery, overlap,
interruption, replay, stale-fence, secret-rotation, outage, rollback, upgrade, cleanup, and no-drift
proofs. Complete support operations documentation is also required.

### Phase 6 — Azure first-class launcher

Tickets:

1. Azure Terraform bootstrap and remote-state pattern;
2. ACR promotion by OCI copy with digest verification, Container Apps Jobs, managed identity, Key
   Vault, logging, alerts, and networking;
3. launcher CLI operations and deployment verification;
4. BigQuery federation feasibility/proof;
5. Snowflake canonical live profile;
6. schedule, retry, parallelism, interruption, rollback, and reconciliation proof.

Exit gate:

- the same release digest passes Azure launcher conformance and the named Azure profile.

Satisfied on 2026-08-12 by the source-free digest
`sha256:a64d89a3beff1b56ed8b3b13f17b67f8f99d87e08ebf48e6ff01381ecdc94d59`, which passed Azure
launcher conformance and the complete named Azure/Snowflake/PostgreSQL/no-catalog/Key-Vault live
profile. Public `0.9.0rc1` then passed the Azure-to-Google refresh, secret, catalog, revocation and
isolated-GCP standard smoke, followed by provider cleanup and retained-GCP no drift. Azure remains
experimental until the applicable Phase 8 qualification passes; Phase 7 implementation did not
begin.

### Phase 7 — OCI first-class launcher

Tickets:

1. OCI Terraform bootstrap and remote-state pattern;
2. OCIR promotion by OCI copy with digest verification, Container Instances, resource principals,
   dynamic groups, Vault, logging, monitoring, and VCN projection;
3. narrow scheduling/launch Function and lifecycle event reconciliation;
4. launcher CLI operations and deployment verification;
5. OCI Vault resolution and credential refresh;
6. PostgreSQL canonical live profile and external-identity feasibility report;
7. schedule, retry, parallelism, interruption, rollback, and reconciliation proof.

Exit gate:

- the same release digest passes OCI launcher conformance and the named OCI profile; unsupported
  cross-cloud identity combinations are explicitly marked rather than bypassed with static cloud
  keys.

Satisfied on 2026-08-13 by public `dander-platform==0.9.0rc17` and source-free runtime index
`sha256:190e9caa082efcd72e9a2a586c082c266e48f99a0bb69b99e30114e3c8c886b9`. The exact GAR/OCIR
content passed the named OCI Container Instances/PostgreSQL/PostgreSQL/no-catalog/OCI-Vault live
profile. The bounded proof covered manual and scheduled execution, replay, overlap exclusion,
interruption, whole-task retry exhaustion, bounded logs, versionless application-secret rotation,
immutable rollback/restoration, alarm-to-topic routing, cleanup, OCI no drift, and retained-GCP no
drift. Direct OCI-to-Google identity remains unsupported and fails closed because OCI resource
principal tokens do not meet the existing generic OIDC refresh/revocation contract; no static key
bypass is permitted. OCI remains experimental until Phase 8 qualification passes. See
`docs/cloud-portability-oci-lifecycle-acceptance.md`.

### Phase 8 — Scale qualification and support release

Tickets:

1. execute the approved benchmark matrix and publish normalized reports, including
   provider-specific throughput, incremental-at-scale, transform, concurrency, bounded-memory,
   crossover, and cost evidence;
2. tune only measured bottlenecks under the optimization gate;
3. run the pairwise provider matrix and every canonical live profile;
4. complete install, permission, network, operations, upgrade, rollback, and troubleshooting docs;
5. run security, distribution, image, infrastructure, and dependency audits;
6. perform release-candidate soak with schedules enabled in each approved profile;
7. freeze the tested compatibility matrix and known limitations.

Exact private RC22 completed item 5 for its source through protected CI and a local exact-artifact
repeat. The later direct-write capability required private arm64 RC23; RC23 passed local
artifact/security preflight and observed equal DIRECT/COPY rows, but completion review invalidated
its byte-threshold objective. An exact-RC22 AWS preflight separately found
that account-local deployment coordinates were absent from the immutable image before Fargate
planning; its disposable data plane was removed exactly. The later AWS root corrections reached
qualification-baseline head `3ea34e2`, which passed all five protected jobs in run `31876449299`;
focused thirteenth review accepted the final version-cleanup permission and current-main
integration. Reconciliation head `0c65e42` passed run `31877158743`; fourteenth review found
two EC2 authorization blockers corrected in `b9735c9`. Correction/current-main head `d8a18ec`
passed run `31878215886`, and focused fifteenth review accepted the correction. Docs-closure head
`6ede9da` passed run `31879161660`; sixteenth review found missing existing-resource dimensions for
route-table, subnet, and VPC-endpoint creation. Commit `e12ee59` adds the qualification-tag-scoped
dependency grants; correction/docs head `0da600b` passed run `31879898267`, and focused seventeenth
review accepted the correction. PR #291 merged the baseline as protected-main commit `3d7783c`,
and exact-main CI run `31882061192` passed all five jobs. PR #298 merged private RC24 as protected
main `c19de39`; exact-main run `31882919709` passed, and source-free multi-platform index
`sha256:b7eadc7e…9488` is published. PR #299 merged its sanitized evidence as `a66ce65`, and
exact-main run `31884123337` passed. RC24 then passed the corrected local PostgreSQL crossover
objective with exact equality/cleanup and a measured disabled DIRECT threshold because no
contiguous DIRECT-winning prefix existed. The later AWS-native execution exposed a Fargate identity
defect, so PR #317 merged private RC25 as protected main `f5935a6`; exact-main run `31902553474`
passed all five jobs, and source-free multi-platform index `sha256:5a0d5520…2238` passed artifact,
selector, and read-only checks. PR #318 merged its sanitized publication evidence as `ae3be54`, and
exact-main run `31903775539` passed all five jobs. The AWS correctness lane is now bound by the
committed RC25/USD 3 objective manifest. PR #319 merged that gate as `c79b3d8`, and exact-main run
`31904727106` passed all five jobs. The first RC25 platform reconciliation then exposed a
stable-name EventBridge tag-read gap before any task ran. Its partial resources were removed
exactly, PR #320 merged the bounded correction as `7155d54`, and the reviewed stage-zero update is
drift-free. A fresh RC25 manual task reached AWS Secrets Manager, PostgreSQL state, and Redshift
credential acquisition, then timed out while the Serverless workgroup cold-started its network
interfaces. Replay did not start; exact saved-plan cleanup removed all 25 platform and 36 data-plane
resources. RC25 remains valid because the required correction is to the qualification connection
timeout, not candidate code. PR #321 merged the sanitized failure record as protected main
`b784318`. The replacement objective preserves exact RC25, its objective set, and the USD 3 ceiling
while binding a 120-second qualification connection timeout under the unchanged 600-second runtime
deadline. The original 30-second objective remains preserved and transfers no result. PR #322
merged the replacement gate as protected main `ea625e3`, and exact-main run `31911384116` passed all
five jobs. PR #323 merged its runbook correction as protected main `c14c6fa`; exact-main run
`31912057557` passed all five jobs. The replacement manual task then connected to Redshift and
created its temporary table, but COPY failed because the runtime database role lacked effective
ASSUMEROLE permission on the explicit S3 staging role. Replay did not start. Saved-plan cleanup
removed all 25 platform and 36 data-plane resources, both states and direct inventories are empty,
and the attempt KMS key is pending deletion on 2026-09-14. PR #324 preserved that failure as
protected main `804496e`; PR #325 merged the exact staging-role grant as protected main `7cea5a8`.
Its exact-main CI run `31914830354` passed all five jobs. PR #326 merged private RC26 as protected
main `f0fe54f`; exact-main run `31915564765` passed all five jobs, and source-free multi-platform
index `sha256:e63aef4b…d28e` passed artifact, selector, attestation, and rootless read-only checks.
PR #327 merged the publication record as protected main `6e9d65e`; exact-main run `31916736418`
passed all five jobs. The fresh exact RC26 objective preserves one manual run, one replay, the
reviewed 120-second connection timeout, exact cleanup, and the existing USD 3 allocation. Its one
manual task completed PostgreSQL state setup, then Redshift connection validation expired after
121,066 ms with no provider operation or row. Replay did not start. The task and private workgroup
shared the reviewed VPC, subnets, and security group; immediate post-failure Data API access worked,
so the exact delay remains unproven. Saved-plan cleanup removed all 25 platform and 36 data-plane
resources, and both states and direct active inventories are empty. RC26 remains current, but the
consumed objective transfers no result. PR #330 merged the sanitized attempt as protected main
`730de0b`; exact-main run `31920702822` passed all five jobs. The replacement objective at
`docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives-v2.json` preserves exact RC26,
the run counts, paused scheduling, exact cleanup, and the existing cumulative USD 3 allocation while
binding a 300-second Redshift connection window below the unchanged 600-second runtime deadline.
That objective reached protected main as `890853d`; exact-main run `31921459727` passed all five
jobs. Its one manual task reached the private Redshift endpoint, authenticated as the exact Fargate
task role, and set `application_name=dander`, but no runtime-user query entered query history before
the Python driver hit the 300-second startup timeout. Replay did not start. Saved-plan cleanup
removed all 25 platform and 36 data-plane resources; both states and direct active inventories are
empty, and the attempt KMS key is pending deletion on 2026-09-14. This is a live-discovered
candidate defect, so a focused connection-startup implementation PR, replacement candidate, and
fresh protected objective must precede another AWS run. No RC24, RC25, or RC26 result transfers.
PR #333 merged the scoped Serverless base-protocol correction as protected main `141fab6`, whose
exact-main run `31924339366` passed all five jobs. PR #334 merged private RC27 as protected main
`d7ac61f`; exact-main run `31925228450` passed all five jobs, and source-free multi-platform index
`sha256:bcf62d2c…4e09c` passed artifact, selector, attestation, and rootless read-only checks under the
unchanged USD 10 ceiling. PR #335 merged the sanitized publication record as protected main
`ea3e260`; exact-main run `31926577710` passed all five jobs. The fresh RC27-bound objective preserves
one manual run, one success-conditional replay, the reviewed 300-second connection timeout, paused
scheduling, exact cleanup, and the cumulative USD 3 allocation. PR #336 merged it as protected main
`c348122`; exact-main run `31927276568` passed all five jobs. The exact manual run and replay both
succeeded with zero provider retries, three duplicate-free Redshift rows, complete assertions,
Glue publication, PostgreSQL state participation, and an empty staging prefix. Reviewed saved-plan
cleanup removed all 25 platform and 36 data-plane resources, both Terraform states and active owned
inventories are empty, and the exact ECR digest remains retained. This closes AWS-native correctness;
provider cost is still `not_evaluated`, and scale, soak, and support remain open. PR #337 merged the
sanitized evidence as protected main `df018e6`; exact-main run `31941210969` passed all five jobs.
PR #338 merged the five RC27 Kubernetes objectives as protected main `6ff041f`; exact-main run
`31942160724` passed all five jobs before cluster creation. One named kind 1.32.2 arm64 cluster then
ran exact RC27 with PostgreSQL state/warehouse, catalog `none`, an existing Secret projection, TLS
PostgreSQL 15.18, a 2 CPU/512 MiB Job limit, a 600-second deadline, zero retries, and reporter-sidecar
collection. Correctness, bulk, incremental, transform, and PostgreSQL-specific failure all passed;
the five reports record exact candidate/objective identity and non-estimated USD 0 cost. Cleanup
left zero Dander schemas, staging relations, namespace Warning events, kind clusters, node
containers, or temporary image tags. This closes the final-candidate named local profile and
five-class Kubernetes launcher-scale slice only; hosted Kubernetes scale/cost, remaining launcher
classes, and soak remain open. See
`docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-scale-attempts.json`.
PR #339 merged the sanitized result as protected main `b73fafc`; exact-main run `31943674409`
passed all five jobs. The next focused DANDER-204 objective binds exact RC27 Kubernetes bounded
memory to the accepted 2.6-million-row/256 MiB PostgreSQL workload, 80% peak-RSS ceiling, 2 CPU,
600-second deadline, zero retries, reporter-sidecar collection, and non-estimated USD 0 cost.
PR #340 merged that objective as protected main `72a422e`; exact-main run `31944524241` passed all
five jobs before cluster creation. The exact RC27 Job then processed 2.7248 GB logical input in
129.180 seconds at 20,127 rows/second with 176,115,712 bytes peak RSS under the reviewed 256 MiB
limit. TLS, the reporter, zero retries/restarts, zero Warning events, database cleanup, USD 0 local
cost, cluster cleanup, and temporary-tag cleanup all passed. One runtime-path preflight failed
before benchmark execution, was corrected against the immutable image, and was isolated by
recreating the owned cluster. No RC22 result transferred. Kubernetes concurrency and crossover are
the next focused launcher classes.
PR #341 merged that sanitized evidence as protected main `f864a2b`; exact-main run `31945860151`
passed all five jobs. The next focused objective binds exact RC27 Kubernetes concurrency to the
same protected 2.6-million-row/256 MiB configuration and approves only four independent 5,000-row
pipelines, stale-fence rejection, throughput measurement, cleanup, and USD 0 local cost. The
accepted script's coupled bounded phase will rerun without gaining a second claim. Protected review
and exact-main CI precede the disposable run; prior incidental and RC22 measurements do not
transfer.
PR #342 merged that objective as protected main `7dc51f8`; exact-main run `31946605370` passed all
five jobs before execution. Exact RC27 then completed four independent 5,000-row pipelines in
334.55 ms at 59,781.789 rows/second, rejected the stale publication fence, and left no Dander schema
or staging relation. TLS, reporter collection, zero retries/restarts, zero Warning events, USD 0
local cost, cluster cleanup, and temporary-tag cleanup passed. One PostgreSQL storage preflight
failed before the candidate Job existed; the harness initialized a user-owned PGDATA subdirectory
and recreated the owned cluster before the passing run. The sanitized concurrency evidence now
awaits protected review; crossover remains a separate fresh objective and evidence lane.
PR #343 merged the concurrency evidence as protected main `bd7489d`; exact-main run `31948875002`
passed all five jobs. The next focused objective binds exact RC27 Kubernetes crossover to the
corrected RC24 workload: COPY and DIRECT at 1, 10, 100, 1,000, and 5,000 rows with 128-byte payloads,
five repetitions, SCD1 canonical equality, a 1 MiB direct ceiling, exact cleanup, and USD 0 local
cost. The disposable kind 1.32.2 arm64 Job keeps 2 CPU/512 MiB, TLS PostgreSQL 15.18, a 600-second
deadline, zero retries, reporter collection, and rootless read-only candidate execution. Protected
review and exact-main CI precede execution; RC24's result and zero threshold do not transfer.
PR #344 merged that objective as protected main `4166afb`; exact-main run `31949803615` passed all
five jobs before execution. Exact RC27 then passed all seven crossover objectives in one disposable
kind 1.32.2 arm64 Job: COPY and DIRECT were canonically equal, DIRECT tied through 10 rows and lost
at larger samples, and the measured environment-specific recommendation was 10 rows / 1,490
logical bytes. The Job processed 61,110 rows in 2.433 seconds at 25,117.139 rows/second with
177,549,312 bytes peak RSS, zero retries/restarts/Warning events, no database residue, USD 0 local
cost, and exact cluster/tag cleanup. This evidence does not change a product default. PR #345
merged the sanitized evidence as protected main `366ce8a`; exact-main run `31951009601` passed all
five jobs. Hosted Kubernetes scale/cost and soak stay open. The next focused objective binds one
disposable zonal GKE Standard bounded-memory final audit to exact RC27 and the already protected
2.6-million-row/2.7248-GB workload, 256 MiB candidate limit, unchanged 80% peak-RSS gate, 2 CPU,
TLS PostgreSQL 15.18, a 600-second deadline, zero candidate retries, reporter collection, and exact
owned-resource cleanup. Its USD 0.50 run ceiling is inside the retained USD 0.75 GCP soak/final
audit allocation, and provider billing must post before the cost objective may pass. Protected
merge and exact-main CI precede any GCP mutation.
PR #346 merged that objective as protected main `b01bf8b`; exact-main run `31952323045` passed all
five jobs. Execution then used main `1256213` after exact-main run `31953203115` also passed all
five jobs, with no benchmark-script drift from exact RC27. The single candidate attempt on a
disposable one-node GKE Standard 1.35.6 zonal cluster processed 2.7248 GB in 356.685 seconds at
7,289.345 rows/second with 179,863,552 bytes peak RSS under the 256 MiB limit and 80% gate. TLS,
reporter retention, zero retries/restarts, PostgreSQL cleanup, and full provider-resource rollback
passed. One infrastructure-only runtime-path preflight failed before candidate execution and was
corrected within the two-attempt ceiling. Provider billing is still pending, so cost and the overall
normalized report remain `not_evaluated`; the raw report's unused `catalog=postgresql` context is
preserved and must be corrected explicitly only in a later derived final report. Hosted Kubernetes
cost, soak, and the remaining provider/profile cells stay open.
The repeated local-kind and hosted-GKE interpreter-path preflights are tracked as DANDER-208.
Current source exposes `dander qualification-run` as the image-owned harness boundary; future
manifests must use it instead of an installation-layout path. RC27 evidence remains unchanged, and
only qualification lanes materially affected after the next candidate require rerun.
DANDER-209 then closed Azure's pre-live immutable-image platform handoff in PR #350 at protected
main `1436092`; exact-main run `31960158477` passed all five jobs. PR #351 prepared private RC28;
its exact protected-main commit `7135b8c` passed run `31961210116` before source-free multi-platform
index `sha256:f8259276…f94959e` was privately published and inspected. Both architectures passed the
stable qualification entrypoint, exact-wheel, rootless read-only, SBOM, and provenance checks;
credential-free GCP, Kubernetes, AWS, and Azure selectors passed. This does not transfer prior
results or close qualification, cost, public-release, or support gates. Azure still requires a
fresh protected exact-candidate objective before any resource mutation.
PR #353 merged that objective as protected main `fdcf14d`; exact-main run `31964559562` passed all
five jobs before mutation. The one permitted manual RC28 Azure execution reached Python and
Snowflake but wrote zero rows: the runtime role lacked database-level `CREATE SCHEMA` authority for
the writer's owned staging-schema lifecycle. No replay or corrective rerun was allowed. Exact
saved-plan cleanup removed all 19 Azure resources across the platform, network/PostgreSQL, and
stage-zero states; the named Snowflake objects and active Azure inventories are empty, with one
inactive purge-protected Key Vault tombstone. Provider cost has not posted. RC28 itself failed
closed correctly and remains reusable. PR #355 closed DANDER-213 as protected main `4815561` after
exact-main run `31973943176` passed all five jobs; the canonical preflight now requires the exact
runtime role's database-level staging-schema authority read-only. Azure correctness and support
stay open until a fresh protected objective is approved with known budget headroom.
A later read-only provider-cost reconciliation found AWS Cost Explorer still denied, Azure actual
cost rows still empty, and no GKE or Compute Engine charge yet visible for the hosted GKE run. The
retained GCP report's displayed August 14/15/16 subtotals were USD 0.00/0.24/0.02, but neither
those rounded daily values nor the USD 1.31 month-to-date subtotal provide complete Phase 8
attribution. Exact aggregate spend and remaining headroom therefore stay unknown; no paid objective
may start and no cost or support gate changes status. See
`docs/evidence/phase8/2026-08-16/provider-cost-reconciliation.json`.
The operator subsequently granted a separate USD 10 additional-spend ceiling and directed the
Azure lane to resume. AWS Cost Explorer now passes with a posted baseline rounding to USD 0.00;
Azure ActualCost access passes with no rows, and the latest GCP observation predates the new
window. The fresh RC28 Azure retry reserves USD 2 as its conservative delayed-billing bound, uses
a new disposable namespace, requires the narrow Snowflake staging grant and corrected canonical
preflight, and allows one manual run plus one success-conditional replay. It must merge and pass
exact-main CI before mutation. See
`docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-retry-objectives.json`.
PR #358 merged that retry objective as protected main `c4ad281`; exact-main run `31981210288`
passed all five jobs. The disposable profile then passed PostgreSQL TLS, corrected Snowflake grant,
exact-image, secret-binding, zero-retry, and canonical preflight checks. One manual RC28 execution
reached Python and Snowflake but failed non-retryably before publication because the portable
Snowflake renderer emitted logical columns and aliases unquoted while Dander's canonical source
columns are quoted lowercase. Snowflake resolved `id` as uppercase `ID` and returned error 904.
Automatic retry remained off and replay remained success-conditional. Exact cleanup removed all
active Azure and named Snowflake objects; a transient subnet dependency required one exact
two-resource recovery plan after Azure completed its scheduled environment deletion. ActualCost
still has no posted row, so the full USD 2 bound remains reserved. PR #360 merged DANDER-214 as
protected main `a2b72f8`; exact-main run `31987252875` passed all five jobs. RC28 is immutable and
must not rerun. A replacement candidate and only the materially affected Azure correctness lane
follow. See
`docs/evidence/phase8/2026-08-17/azure-snowflake-rc28-correctness-retry-attempt.json`.
Other exact-objective classes and
final-candidate reruns remain. Each objective continues
from a fresh protected-main branch; only materially affected
evidence and the eventual closure matrix are rerun. Items 1, 3, 4, 6,
and 7 stay open where provider/profile evidence is incomplete;
neither audit promotes an experimental profile.

Exit gate:

- every first-class definition is evidenced, the complete suite passes, provider live proofs are
  current, scale/cost results are attached, and no material adversarial-review blocker remains.

## 13. Pull-request boundaries

Keep each PR independently reviewable and releasable:

- contract/schema changes before provider implementations;
- characterization and compatibility tests in the same PR as boundary moves;
- one state backend, warehouse, catalog, secret provider, or launcher per implementation PR;
- infrastructure module and its deployment verifier together;
- live proof evidence after implementation, not mixed with unrelated features;
- documentation and compatibility-matrix update in every provider PR;
- no PR combines a new warehouse and a new launcher unless it is the explicit vertical-slice gate.

Every substantial PR must update the decision log, relevant ticket, known limitations, distribution
contents, `HANDOFF.md`, and only the checks actually run.

## 14. Major risks and controls

| Risk | Smallest adequate control |
|---|---|
| Generic interfaces hide BigQuery semantics | Characterization tests and small capability protocols before moving code |
| Cross-state/warehouse atomicity is impossible | Destination-side target fence plus replay-safe post-commit watermark CAS |
| A state-backend cutover revives old tokens | Pause/drain, destination cutover lock, authority epoch advance, migration verification, then resume |
| SQL dialect drift corrupts transforms | Parsed model representation, per-dialect compiler, provider conformance fixtures |
| Provider defaults produce silently different rows | Canonical null/time/decimal/text/cast/JSON/window semantics plus rejection fixtures |
| Arbitrary existing BigQuery SQL cannot be portable | Explicit dialect metadata, portable subset, reviewed provider variants |
| Schema mappings are lossy | Canonical types, explicit fallback selection, fail-closed unsupported diagnostics |
| Provider matrix becomes untestable | Adapter conformance + pairwise matrix + named canonical profiles |
| Cross-cloud egress is slow or expensive | Region-aware validation, benchmark cost/latency, explicit warnings and support status |
| OCI scheduling is not a native batch-job primitive | Narrow idempotent launch Function and lifecycle state machine |
| Fargate or OCI tasks outlive the declared deadline | Provider-side execution controller/reconciler stops work and owns bounded whole-task attempts |
| Cross-cloud login works once but not for a long run | Live expiry/refresh/revocation proof for each allowlisted identity path |
| Secrets leak into Terraform state or logs | References only, native injection/runtime fetch, diff/log secret scans |
| Full image becomes too large or slow to start | Lazy imports, measured image/startup budgets, provider extras for library installs |
| Refactor breaks the proven GCP slice | BigQuery/Cloud Run parity gates precede every new provider lane |
| Paid live testing expands silently | Explicit budget, approval, retained provider job IDs, teardown/inventory report |
| Multiple chats overwrite active work | Dedicated worktrees and branches; no edits or merges across active WIP without review |

## 15. Decisions required before implementation

These decisions materially affect the product and must be recorded before Phase 2:

1. Whether one project may use multiple named platform profiles concurrently. Recommended: yes,
   because it enables shadow migration and avoids a second configuration format later.
2. Whether the official image contains all provider adapters. Recommended: yes, while Python
   installations retain extras, because one portable artifact is the core promise.
3. PostgreSQL minimum version. Recommended: PostgreSQL 15+ for a stable `MERGE` baseline while
   retaining `ON CONFLICT` where it is the safer write primitive.
4. Whether Dataplex and Glue must represent non-native warehouses. Recommended: yes through custom
   entries/direct table metadata, otherwise catalog selection is coupled to warehouse selection.
5. OCI native launcher shape. Recommended: Container Instances plus a narrow launch Function;
   OKE remains the Kubernetes launcher.
6. Initial SLO and paid-test budgets. Required before any scale claim or large live benchmark.

## 16. Completion checklist

- [ ] Versioned raw OCI runtime contract is implemented and documented.
- [ ] One immutable OCI index digest passes all launcher conformance suites.
- [ ] BigQuery, Snowflake, Redshift, and PostgreSQL pass warehouse conformance and live proofs.
- [ ] BigQuery and PostgreSQL pass identical state-conformance and fault suites.
- [ ] Dataplex, Glue, and none pass catalog conformance.
- [ ] Environment, GCP, AWS, Azure launcher projection, and OCI Vault secret paths are proven.
- [ ] Cloud Run, Fargate, Kubernetes/Helm, Azure, and OCI launchers meet the first-class definition.
- [ ] Every canonical profile passes manual and scheduled execution, replay, interruption, upgrade,
  rollback, and no-change reconciliation.
- [ ] Pairwise compatibility matrix and known unsupported combinations are published.
- [ ] Scale and cost reports satisfy the approved SLOs for every warehouse and launcher.
- [ ] Full tests, lint, typing, packaging, container, Terraform, Helm, live proofs, and release soak
  pass without weakening existing tests.

## 17. Provider references anchoring the plan

- [Cloud Run Jobs](https://cloud.google.com/run/docs/create-jobs)
- [Amazon ECS scheduled tasks with EventBridge Scheduler](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html)
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
- [Kubernetes CronJobs](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)
- [Azure Container Apps Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
- [OCI Container Instances](https://docs.oracle.com/en-us/iaas/Content/container-instances/overview-of-container-instances.htm)
- [OCI Resource Scheduler supported resources](https://docs.oracle.com/en-us/iaas/Content/resource-scheduler/concepts/resourcescheduleroverview-about.htm)
- [OCI scheduled Functions](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsscheduling.htm)
- [Step Functions integration with ECS/Fargate](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html)
- [EventBridge Scheduler to Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/using-eventbridge-scheduler.html)
- [Artifact Registry image copying](https://cloud.google.com/artifact-registry/docs/docker/copy-images)
- [Snowflake data loading](https://docs.snowflake.com/en/user-guide/data-load-overview)
- [Snowflake warehouse considerations](https://docs.snowflake.com/en/user-guide/warehouses-considerations)
- [Amazon Redshift COPY](https://docs.aws.amazon.com/redshift/latest/dg/r_COPY.html)
- [Amazon Redshift MERGE](https://docs.aws.amazon.com/redshift/latest/dg/r_MERGE.html)
- [PostgreSQL COPY](https://www.postgresql.org/docs/current/sql-copy.html)
- [PostgreSQL MERGE](https://www.postgresql.org/docs/current/sql-merge.html)
- [AWS Glue Data Catalog](https://docs.aws.amazon.com/glue/latest/dg/catalog-and-crawler.html)
- [Google Workload Identity Federation for AWS and Azure](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds)
