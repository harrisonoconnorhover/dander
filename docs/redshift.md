# Experimental Amazon Redshift warehouse

Redshift is an experimental warehouse adapter, not a supported Dander profile. The current slice
proves native database/schema coordinates, bounded IAM-only bulk loading, all five fenced ingestion
write modes, fenced table and incremental models, and replace-mode graph targets before a live
support claim.

## Configuration

Select Redshift in a version 2 platform profile. This provisioned-cluster example stores resource
identifiers but no AWS access keys or database password:

```yaml
platforms:
  redshift_test:
    warehouse:
      provider: redshift
      deployment: provisioned
      host: example.abc123.us-east-1.redshift.amazonaws.com
      database: analytics
      schema: raw
      db_user: dander_user
      region: us-east-1
      cluster_identifier: dander-test
      copy_role_arn: arn:aws:iam::123456789012:role/DanderRedshiftCopy
      staging_bucket: dander-redshift-staging
      direct_max_rows: 0
      direct_max_logical_bytes: 0
    state:
      provider: postgresql
      authority_id: postgresql:redshift-test
    catalog:
      provider: none
    secrets:
      provider: environment
```

Serverless uses `deployment: serverless` and `workgroup_name` instead of `cluster_identifier` and
`db_user`. Dander obtains temporary IAM credentials from the ordinary AWS SDK chain. The configured
COPY role must read the dedicated same-region staging prefix.

COPY remains the default. Set both direct limits to positive values to opt small complete endpoint
streams into bounded parameterized inserts; for example, `direct_max_rows: 1000` and
`direct_max_logical_bytes: 1048576`. Dander selects direct loading only after the entire stream
fits both limits. Otherwise it falls back once to the existing Parquet/S3/COPY path without losing
the inspected prefix. The S3 bucket and COPY role remain required because they handle that fallback.

## Current contract

- `database + schema + relation` become one canonical `RelationRef`; GCP aliases are not used.
- Bounded, checksummed Parquet parts load through a mandatory same-region S3 manifest and `COPY` by
  default. An explicit paired threshold can select bounded parameterized inserts for a complete
  small endpoint without contacting S3.
- Replace, SCD1, SCD2, snapshot, and incremental publication reuse the same fenced publication path
  after either direct or COPY staging.
- Every mode's target mutation, replay history, and exact destination fencing-token touch commit
  together.
- SCD1 and incremental input is deterministically de-duplicated by business key; incremental writes
  additionally reject cursor regression.
- Replace publishes one complete logical stream with replay-safe `DELETE`/`INSERT`; empty replaces
  are also fenced and replay-safe.
- SCD2 uses transaction-stable `SYSDATE` values for `valid_from`/`valid_to`, and snapshot mode
  appends only distinct full rows for a non-null declared snapshot field.
- A canonical JSON field maps to `SUPER` only with the exact `redshift/fallback=super` extension.
  Dander rejects non-finite numbers and non-string object keys locally, stages deterministic UTF-8
  JSON as `VARBYTE(16777216)`, and calls `JSON_PARSE` inside the fenced publication.
- Only declared nullable ingestion columns evolve automatically; drift fails closed.
- Portable or Redshift-authored table models use fenced `DELETE`/`INSERT` replacement.
- Incremental models collapse duplicate keys deterministically and reject cursor regression.
- Generic not-null, uniqueness, accepted-value, and relationship assertions run after publication.
- The complete selected transform DAG preflights before any provider connection or mutation.
- Executable graph targets render the existing provider-neutral relational AST as Redshift SQL,
  preflight as one set, and publish through the same fenced table-replacement path.
- Completed COPY and CTAS operations emit provider-neutral telemetry. A bounded, same-session
  lookup enriches those operations with Redshift queue/execution time, bytes, loaded rows, spill
  blocks, service class, compute type, and COPY job ID when the system views are available.

## Deliberate limits

Scalar fields and explicit JSON-to-`SUPER` fallback are implemented for direct/COPY ingestion and
table/incremental models. `SUPER` fields cannot be business keys, cursors, or snapshot fields.
ARRAY/RECORD fallbacks remain unavailable. COPY retains its 4 MB staged-row guard, while an
explicitly bounded direct load may use the declared 16 MB VARBYTE/SUPER boundary. The ordinary
hosted source runner still selects SCD1.
Graph execution retains the shared single-connector, replace-target-only boundary; safe casts fail
preflight because Redshift cannot yet preserve their canonical semantics. Views, live concurrency
proof, infrastructure provisioning, and support promotion remain separate work. Use
`catalog.provider: none` for this experimental path.

Telemetry is best-effort and never carries SQL text, error text, S3 locations, or record data.
Direct-load telemetry uses exact local row/byte/duration counters and deliberately has no query ID;
DB-API `executemany` does not provide one honest Redshift query ID for the complete batch.
Multi-statement fenced publication and assertion operations report local duration and affected-row
counters without claiming a potentially incorrect Redshift query ID. Missing or delayed system
history leaves those base counters unchanged.

Declare the fallback on the field; bare canonical JSON continues to fail closed:

```yaml
- name: payload
  type: JSON
  extensions:
    - provider: redshift
      name: fallback
      value: super
```
