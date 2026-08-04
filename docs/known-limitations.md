# Known limitations

Dander `0.2.x` is alpha and proves a focused GCP-native vertical slice. It is not yet suitable for
an unattended production system containing business-critical data.

## Ingestion and schemas

- Hosted SCD1 and sandbox replacement consume bounded batches. Direct SCD2, snapshot,
  incremental-writer, and Storage Write orchestration retain logical-batch behavior.
- Automatic deployed-schema evolution adds only explicitly declared top-level `NULLABLE` fields.
  Nested additions, type or mode changes, removals, and undeclared drift fail before loading.
- Project-defined hosted pipelines require declared raw schemas. Direct legacy source execution
  may still infer a schema and is deprecated.
- HubSpot companies extraction performs a full endpoint read followed by idempotent SCD1 merge;
  the selected endpoint does not receive a server-side incremental filter.
- ServiceNow incidents extraction performs a stably ordered full endpoint read. It does not use a
  timestamp watermark with offset paging because that combination can skip moving records.
- Source hard deletes are not propagated by the first ServiceNow incidents slice.
- NetSuite customers are simulator-validated only. The first SuiteQL slice performs a full read,
  is capped by NetSuite's 100,000-result SuiteQL REST limit, and has not passed tenant-specific
  role, field-availability, or authentication acceptance. It is not supported in public `0.2.0`.
- Greenhouse Job Board extraction is a full refresh but does not delete jobs that disappear from
  the public board.

## Publication and concurrency

- BigQuery DML finalizers are transactionally fenced. Cloud replace publication is not advertised
  as transactionally fenced for the direct record writer. PipelineGraph replacement uses a
  separate staged, transactionally fenced DML finalizer.
- Executable PipelineGraphs currently support one connector YAML per hosted pipeline and
  `replace` targets only. Graph field tests and graph-to-Dataplex publication fail closed.
- A crashed sandbox staging table relies on configured expiration for cleanup. Handled failures
  remove their staging table immediately.
- Run history stores non-sensitive stage and aggregate counts, not exception text or source rows.
  Use the Cloud Run execution logs for diagnosis.

## Platform and cost

- `dander init` provisions Dander inside an existing GCP project; it does not create a project or
  attach billing.
- The simulation-first USD 5 cost guard verifies configuration but is not a hard spending cap.
  Cloud charges can be delayed, and enabled GCP services may be billable.
- The starter requires Python 3.12, Terraform 1.9 or newer, Docker Buildx, Google Cloud CLI, and an
  authenticated administrator.
- Dataplex and Storage Write remain optional paths and are not part of the supported `0.2.x`
  operator trial.

## Support boundary

`0.2.x` receives fixes only. New connectors, commands, subsystems, manifest capabilities, and
broader writer orchestration enter through `0.3.0` or a later minor. Only the latest patch in the
current public `0.x` minor is supported; see [SECURITY.md](../SECURITY.md).
