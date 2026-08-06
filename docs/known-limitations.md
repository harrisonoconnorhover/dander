# Known limitations

Dander `0.6.x` is alpha and proves a focused GCP-native vertical slice. It is not yet suitable for
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
- Salesforce reads Accounts, Contacts, Opportunities, and Users only. QueryAll preserves visible
  soft-deletion tombstones, but hard-deleted or purged records cannot be recovered and provider
  write-back is not supported. ServiceNow remains one read-only incident slice.
- NetSuite customers are simulator-validated only. The first SuiteQL slice performs a full read,
  is capped by NetSuite's 100,000-result SuiteQL REST limit, and has not passed tenant-specific
  role, field-availability, or authentication acceptance. It is not in the current public support
  surface.
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
- Druff is a public static interface, not a hosted control plane. Saving, validation, execution,
  status, and deployment preview require an operator-started Dander loopback service. Druff does
  not write `dander.yaml` or apply Terraform.

## Platform and cost

- `dander init` provisions Dander inside an existing GCP project; it does not create a project or
  attach billing.
- The simulation-first USD 5 cost guard verifies configuration but is not a hard spending cap.
  Cloud charges can be delayed, and enabled GCP services may be billable.
- The starter requires Python 3.12, Terraform 1.9 or newer, Docker Buildx, Google Cloud CLI, and an
  authenticated administrator.
- Dataplex and Storage Write remain optional paths and are not exercised by the retained operator
  trial.

## Support boundary

`0.6.x` receives candidate fixes only until the minor is promoted. Dander `0.5.1` remains the
latest stable alpha during candidate acceptance. Only the newest candidate and the latest patch
in the current stable `0.x` minor are supported; see [SECURITY.md](../SECURITY.md).
