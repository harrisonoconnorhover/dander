# Runtime compatibility matrix

Dander publishes the state/warehouse combinations tested by the installed package. Read it without
provider credentials or network access:

```bash
dander runtime compatibility
```

The output is the machine-readable `io.dander.runtime.compatibility/v1` contract. Selection fails
before provider construction when a pair is absent or marked `unsupported`.

| State | Warehouse | Status | Current evidence |
|---|---|---|---|
| BigQuery | BigQuery | supported | Released GCP profile and BigQuery regression baseline |
| BigQuery | PostgreSQL | experimental | Local cross-backend token/fence publication proof |
| BigQuery | Snowflake | experimental | Local staged-SCD1, fenced transforms, schema, replay, and fence conformance |
| BigQuery | Redshift | experimental | Local IAM/S3 `COPY`, SCD1 replay, fenced transforms, and destination-fence conformance |
| PostgreSQL | BigQuery | unsupported | Not every BigQuery write mode uses destination fencing |
| PostgreSQL | PostgreSQL | experimental | Local native profile, state/warehouse conformance, and benchmarks |
| PostgreSQL | Snowflake | experimental | Local staged-SCD1, transforms, and destination-fence conformance |
| PostgreSQL | Redshift | experimental | Local IAM/S3 `COPY`, SCD1 replay, fenced transforms, and destination-fence conformance |

`experimental` means the code path is executable and tested locally; it is not a hosted support
claim. The packaged `runtime-capabilities.json` remains the supported launcher/profile boundary.
Promotion requires the named live profile, identity, failure, rollback, and no-drift evidence from
the cloud-portability plan. Adding an adapter does not silently add a matrix row.
