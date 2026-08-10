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
| BigQuery | Snowflake | experimental | Local five-mode writes, fenced transforms/graphs, schema, replay, and fence conformance |
| BigQuery | Redshift | experimental | Local IAM/S3 `COPY`, SCD1 replay, fenced transforms, and destination-fence conformance |
| PostgreSQL | BigQuery | unsupported | Not every BigQuery write mode uses destination fencing |
| PostgreSQL | PostgreSQL | experimental | Local native profile, state/warehouse conformance, and benchmarks |
| PostgreSQL | Snowflake | experimental | Local five-mode writes, transforms/graphs, and destination-fence conformance |
| PostgreSQL | Redshift | experimental | Local IAM/S3 `COPY`, SCD1 replay, fenced transforms, and destination-fence conformance |

`experimental` means the code path is executable and tested locally; it is not a hosted support
claim. The packaged `runtime-capabilities.json` remains the supported launcher/profile boundary.
Promotion requires the named live profile, identity, failure, rollback, and no-drift evidence from
the cloud-portability plan. Adding an adapter does not silently add a matrix row.

Catalog selection is independent from this state/warehouse matrix. Dataplex is supported only in
the released GCP profile, `none` is a complete no-cloud-mutation provider, and AWS Glue is an
experimental direct API projection with local create/update/readback conformance only.

## Warehouse capabilities

The same command publishes the exact implemented warehouse surface. These declarations are
capabilities, not support promotion: Snowflake, Redshift, and PostgreSQL remain experimental until
their named live profiles pass.

| Warehouse | Ingestion modes | Transport | Canonical schema | Models | Graphs | Fence |
|---|---|---|---|---|---|---|
| BigQuery | all five | load job, Storage Write | all v1 types; decimal 38, time 6; no nested arrays | yes | yes | yes |
| PostgreSQL | SCD1 | COPY | all v1 types; decimal 1000, time 6 | yes | no | yes |
| Redshift | all five | COPY | scalar types; decimal 38, time 6; explicit JSON-to-SUPER fallback | yes | yes | yes |
| Snowflake | all five | direct, COPY | scalar types; decimal 38, time 9; explicit JSON-to-VARIANT fallback | yes | yes | yes |

Portable-provider schema validation uses these declarations before source extraction, staging
upload, or destination mutation. BigQuery retains its existing provider-native v1 schema path so
types without lossless canonical mappings remain compatible. An unsupported portable field reports
the provider, field path, type or precision, and supported limit. The packaged JSON and runtime
capability constants are checked for drift in the test suite.
