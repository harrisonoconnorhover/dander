# AWS Glue Data Catalog

AWS Glue is an experimental external catalog provider for version 2 platform profiles. It consumes
the same canonical `CatalogAsset` records as the local semantic registry and Dataplex; it does not
change warehouse execution or make an AWS profile supported.

```yaml
# dander.yaml
version: 2
pipelines:
  salesforce_crm:
    source: salesforce
    models: [stg_salesforce__accounts]
    publish_catalog: true
```

```yaml
# dander.platforms.yaml
version: 1
platforms:
  aws:
    warehouse:
      provider: redshift
      # Redshift settings omitted here.
    state:
      provider: postgresql
      # PostgreSQL settings omitted here.
    catalog:
      provider: glue
      region: us-east-1
      catalog_id: "123456789012"
      database_prefix: dander
      connection_name: analytics-redshift # optional metadata only
    secrets:
      provider: environment
```

The runtime uses ambient boto3 credentials. Do not put AWS access keys in either manifest. The
runtime principal needs only these actions for the selected catalog and Dander databases/tables:

- `glue:GetDatabase`, `glue:CreateDatabase`, and `glue:UpdateDatabase`
- `glue:GetTable`, `glue:CreateTable`, and `glue:UpdateTable`

Dander never calls a Glue crawler and never deletes a database or table. It maps canonical
`catalog + namespace` coordinates to one lowercase Glue database and the canonical relation name
to one lowercase table. A short digest is added only when normalization could otherwise be lossy
or ambiguous.

Publication owns the table description, owner, columns, `classification=dander`, and parameters
whose names begin with `dander.`. It preserves unrelated table parameters, database parameters,
storage-descriptor fields, partition metadata, and non-Dander column parameters. Readback returns
only the normalized Dander-owned projection for deterministic comparison.

Glue columns retain each canonical Dander type in `dander.logical_type`. Types without a direct
Hive representation, including JSON and semi-structured values, use a `string` presentation type
without changing the warehouse schema. Warehouse-backed relations do not imply an S3 location;
the optional connection name is catalog metadata and does not create or validate a Glue
connection.

This adapter is locally stateful-fake/conformance tested. IAM provisioning, a live Glue proof,
cross-cloud identity, tags/Lake Formation behavior, and support promotion remain separate gates.
