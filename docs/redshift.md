# Experimental Amazon Redshift warehouse

Redshift is an experimental warehouse adapter, not a supported Dander profile. The current slice
proves native database/schema coordinates, bounded IAM-only bulk loading, and fenced table and
incremental models before broader write modes or a live support claim.

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
- SCD1 publication, replay history, and the exact destination fencing-token touch commit together.
- Only declared nullable ingestion columns evolve automatically; drift fails closed.
- Portable or Redshift-authored table models use fenced `DELETE`/`INSERT` replacement.
- Incremental models collapse duplicate keys deterministically and reject cursor regression.
- Generic not-null, uniqueness, accepted-value, and relationship assertions run after publication.
- The complete selected transform DAG preflights before any provider connection or mutation.

## Deliberate limits

Only scalar SCD1 ingestion plus table and incremental model materializations are implemented.
Views, graphs, replace, SCD2, snapshot, `SUPER`, live concurrency proof, infrastructure
provisioning, and support promotion remain separate work. Use `catalog.provider: none` for this
experimental path.
