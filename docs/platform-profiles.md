# Platform profiles and version 2 projects

Dander version 2 separates reusable pipeline intent from environment-specific deployment choices:

- `dander.yaml` contains connector plugins, sources, graphs/models, tests, and catalog intent.
- `dander.platforms.yaml` contains named warehouse, state, catalog, secret, and launcher selections,
  plus runtime limits, schedules, secret references, and provider resource names.

The first supported hosted profile remains BigQuery, BigQuery state, Dataplex (or no cloud
catalog), GCP Secret Manager, and Cloud Run. Version 2 may select PostgreSQL 15+ for both durable
state and the warehouse, but that composition remains locally qualified until the Kubernetes
launcher and live-profile gates pass. A provider name outside the installed contract fails
validation; adapter registration alone does not imply hosted support. AWS Glue is available as an
experimental catalog projection only.

One logical project may have multiple named platforms and deployments. `dander validate` and
`dander run` accept `--deployment`; the OCI runtime's `--platform` value selects the named
deployment and may use an explicit `--platforms-config` path. Other current GCP commands require a
single deployment until their provider-neutral planning path is introduced.
When a project has exactly one deployment, Dander selects it deterministically; multiple
deployments never fall back to an arbitrary default.

## Azure experimental profiles

The named `azure_container_apps` profile combining Snowflake, PostgreSQL state, no catalog, and
Azure Key Vault passed the bounded Phase 6 live lifecycle. The separate Azure-to-Google profile
passed on public `0.9.0rc1`: BigQuery access across credential refresh plus GCP Secret Manager,
Dataplex, revocation, cleanup, and isolated-GCP smoke. Both remain experimental: configuration
outside these named shapes fails closed, and
Phase 8 scale, cost, soak, pairwise-profile, and release qualification are still required before a
support promotion. Dander creates neither an Azure subscription nor the delegated Container Apps
subnet required by the Key Vault profile.

## Migrate a version 1 project

Version 1 combined manifests remain supported during the compatibility window. First run the
read-only check:

```bash
dander config migrate --config dander.yaml --check
```

The check renders both files in memory, resolves the generated GCP/Cloud Run deployment, and
requires its platform settings, plugin pins, pipeline behavior, Terraform pipeline projection, and
stable resource names to equal the version 1 result. It changes no file.

Then write the deterministic split:

```bash
dander config migrate --config dander.yaml
dander validate --config dander.yaml --platforms-config dander.platforms.yaml
```

Migration refuses to overwrite an existing `dander.platforms.yaml`. Commit and review both files
together. Existing Terraform addresses do not change merely because the equivalent configuration
is represented in two files.

The runtime composes the selected warehouse and state through one API-v1 provider registry.
Registration loads only a small configuration model; selecting and building a provider loads its
implementation and SDK dependencies. BigQuery behavior and names remain unchanged. Dataplex keeps
aspect-only updates and normalized readback; selecting `none` loads no Dataplex implementation or
credentials. Glue uses exact region/account configuration, ambient AWS identity, direct
database/table APIs, and normalized readback; see [AWS Glue](aws-glue.md). GCP Secret Manager
preserves its existing environment indirection and audit behavior;
environment-only resolution remains local or launcher-injected because Cloud Run requires managed
secret references. The registry is a construction contract, not a support claim.

Warehouse adapters exchange [canonical relation and schema contracts](canonical-schema.md).
Existing BigQuery connector/writer declarations remain valid and expose a one-way canonical view;
the selected provider remains responsible for physical identifier rendering and type mapping.

Provider SDKs can be installed independently with extras such as
`dander-platform[postgres]` or `dander-platform[aws]`. The official OCI runtime installs
`dander-platform[runtime-all]` so one immutable image can later serve any first-class adapter.
Installing an extra does not make its provider selectable; configuration remains gated by the
registered adapter and capability manifest.

## PostgreSQL durable state

Select PostgreSQL only in `dander.platforms.yaml`; keep the connection string out of Git:

```yaml
platforms:
  portable:
    warehouse:
      provider: postgresql
      database: dander
      dsn_env: DANDER_POSTGRES_DSN
    state:
      provider: postgresql
      authority_id: postgresql:portable-state
      authority_epoch: 1
      dsn_env: DANDER_POSTGRES_DSN
      schema_name: dander_meta
      pool_min_size: 1
      pool_max_size: 5
      pool_timeout_seconds: 10
      lease_seconds: 120
      terminal_history_retention_days: 90
    catalog:
      provider: none
    secrets:
      provider: environment
```

Install `dander-platform[postgres]` for a provider-specific environment, or use the official
`runtime-all` image. Inject `DANDER_POSTGRES_DSN` through the launcher's existing secret binding;
the value is never part of either manifest. Dander creates and migrates only the configured schema.
It uses a bounded pool, PostgreSQL server time for leases, atomic watermark comparison, sanitized
run history, and deterministic JSONB metadata snapshots.

`authority_id` is a stable, non-secret identifier for this state deployment. Do not reuse it for a
different database or change `authority_epoch` outside a reviewed state-backend cutover.

PostgreSQL state with a PostgreSQL warehouse is executable. BigQuery state with PostgreSQL is also
transactionally fenced, although it is not a qualified hosted profile. PostgreSQL state with a
BigQuery warehouse remains fail-closed until every BigQuery write mode uses the destination-side
target fence rather than only the state-side lease transaction. Run `dander runtime compatibility`
to inspect this package's exact matrix; see [Runtime compatibility matrix](compatibility-matrix.md).

Terminal `succeeded`, `failed`, and `skipped` history older than the configured retention is
removed when migrations run. Active runs and `interrupted_run` records are retained. Dander
refuses a state ledger newer than the running package. Changing state backends is not an online
migration: keep schedules paused until the destination-fence and cutover workflow is complete.

## PostgreSQL warehouse adapter

The PostgreSQL warehouse adapter is selectable but not yet a supported hosted profile. It accepts
declared schemas, creates database-local schemas and relations, and defaults to streaming records
through PostgreSQL `COPY`. An opt-in direct path may be configured with both `direct_max_rows` and
`direct_max_logical_bytes`; it is selected only when the complete endpoint fits both bounds and
otherwise preserves the prefix and falls back to COPY. Both settings default to zero. Private local
RC23 recorded a 10-row/1,400-byte crossover for its exact TLS PostgreSQL 15.18 shape, but
completion review invalidated that byte threshold because it omitted writer-counted field names.
The corrected 1,490-byte calculation remains unqualified until the replacement candidate reruns it.
Enabling direct selection makes every mode consume one bounded endpoint so executor batching cannot
change the physical-path decision.
Every mode publishes inside the same transactionally verified destination fence. Temporary staging
uses `ON COMMIT DROP`; nullable top-level columns may be added when additive evolution is selected.

The configured database must already exist. Runtime-created connections require TLS, and the DSN
is supplied only through the configured environment variable. The adapter supports PostgreSQL
15+, replace, SCD1, SCD2, snapshot, and incremental ingestion, canonical scalar/array/JSON
mappings, portable or PostgreSQL-exact model SQL, table/view/incremental materialization, and the
four generic assertions. SCD1 and incremental use deterministic business-key de-duplication;
incremental publication rejects cursor regression. Snapshot comparison is null-safe, replace is
empty-source safe, and SCD2 uses transaction-stable validity timestamps. Every materialization is
claimed and published through its destination target fence. The local native-profile proof covers
bounded ingestion, replay, transforms, assertions, metadata, run history, watermarks, and leases.
Replace-mode graphs render the provider-neutral relational AST as PostgreSQL SQL and publish stable
targets inside the same destination-fence transaction. Cross-database source or target coordinates
and graph safe casts fail before provider mutation. The ordinary hosted source runner still selects
SCD1. The packaged existing-cluster Kubernetes/Helm launcher can
render and verify this profile without cloud-specific identity assumptions, but no live cluster
qualification has passed. Until that acceptance does, this adapter must not be represented as a
supported hosted PostgreSQL deployment. See [Kubernetes existing-cluster launcher](kubernetes.md).
The reproducible local scale harness and its non-qualification boundary are documented in
[PostgreSQL portability benchmarks](postgresql-benchmarks.md).
