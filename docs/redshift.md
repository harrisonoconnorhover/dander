# Experimental Amazon Redshift warehouse

Redshift is an experimental warehouse adapter, not a supported Dander profile. The current slice
proves native database/schema coordinates, bounded IAM-only bulk loading, all five fenced ingestion
write modes, and fenced table and incremental models before a live support claim.

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

## Current contract

- `database + schema + relation` become one canonical `RelationRef`; GCP aliases are not used.
- Bounded, checksummed Parquet parts load through a mandatory same-region S3 manifest and `COPY`.
- Replace, SCD1, SCD2, snapshot, and incremental publication reuse the same bounded COPY path.
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

## Deliberate limits

Scalar fields and explicit JSON-to-`SUPER` fallback are implemented for COPY ingestion and
table/incremental models. `SUPER` fields cannot be business keys, cursors, or snapshot fields.
ARRAY/RECORD fallbacks remain unavailable, and the current staged-row guard is 4 MB even though the
declared VARBYTE/SUPER boundary is larger. The ordinary hosted source runner still selects SCD1.
Views, graphs, live concurrency proof, infrastructure provisioning, and support promotion remain
separate work. Use `catalog.provider: none` for this experimental path.

Declare the fallback on the field; bare canonical JSON continues to fail closed:

```yaml
- name: payload
  type: JSON
  extensions:
    - provider: redshift
      name: fallback
      value: super
```
