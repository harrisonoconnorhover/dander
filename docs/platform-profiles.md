# Platform profiles and version 2 projects

Dander version 2 separates reusable pipeline intent from environment-specific deployment choices:

- `dander.yaml` contains connector plugins, sources, graphs/models, tests, and catalog intent.
- `dander.platforms.yaml` contains named warehouse, state, catalog, secret, and launcher selections,
  plus runtime limits, schedules, secret references, and provider resource names.

The first supported hosted profile remains BigQuery, BigQuery state, Dataplex (or no cloud
catalog), GCP Secret Manager, and Cloud Run. PostgreSQL 15 or newer is implemented as an
alternative durable-state backend for version 2 projects, but the complete PostgreSQL/Kubernetes
profile is not yet live-qualified. A provider name outside the installed and supported contract
fails validation; configuration alone does not imply profile support.

One logical project may have multiple named platforms and deployments. `dander validate` accepts
`--deployment`; the Python configuration loader accepts the same explicit selection. Other current
GCP commands require a single deployment until the shared provider factories are introduced.
When a project has exactly one deployment, Dander selects it deterministically; multiple
deployments never fall back to an arbitrary default.

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

The current runtime continues to support only the GCP compatibility composition. The internal
factory contract provides one API-v1 registry across warehouse, state, catalog, secret, and
launcher categories. Registration loads only a small configuration model; selecting and building
a provider loads its implementation and SDK dependencies. BigQuery writer, transform, lease,
watermark, run-history, metadata-store, external catalog-publisher, and secret-store construction
now use that boundary. State migrations are explicit and versioned, but retain the existing
BigQuery table names and semantics. Dataplex keeps aspect-only updates and normalized readback;
selecting `none` loads no Dataplex implementation or credentials. GCP Secret Manager preserves its
existing environment indirection and audit behavior; environment-only resolution remains local
because Cloud Run requires managed secret references. Cloud Run execution templates now pass
through the same lazy provider boundary while delegating to the accepted projection unchanged.
The registry is a construction contract, not a support claim.

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
      provider: bigquery
      location: US
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

The current BigQuery warehouse/PostgreSQL state combination fails closed at execution because its
cross-backend destination fence is the next portability ticket. The state adapter is available for
conformance and composition work now; it is not a shortcut around that publication boundary.

Terminal `succeeded`, `failed`, and `skipped` history older than the configured retention is
removed when migrations run. Active runs and `interrupted_run` records are retained. Dander
refuses a state ledger newer than the running package. Changing state backends is not an online
migration: keep schedules paused until the destination-fence and cutover workflow is complete.
