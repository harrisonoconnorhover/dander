# Known limitations

Dander `0.8.x` is beta and proves a focused GCP-native vertical slice. It remains pre-1.0 software;
evaluate these limits before using it for an unattended system containing business-critical data.

The Phase 5 warehouse implementations and the
[shared four-warehouse deterministic fixture](warehouse-correctness-conformance.md) pass on one
protected-main commit with equal normalized rows and exact cleanup. This closes the Phase 5
correctness gate; it does not promote Snowflake, Redshift, PostgreSQL/Kubernetes, or any new
state/warehouse pair to supported. Scale, cost, soak, pairwise-profile, and release qualification
remain Phase 8 work.

- AWS ECS/Fargate remains experimental rather than supported. The named Fargate-to-BigQuery/GCP
  composition has passed source-free manual and scheduled execution, replay, interruption,
  renewable keyless identity, alert routing, image rollback, cleanup, and no-drift acceptance.
  The disposable proof topic had no human subscriber, and the profile has not completed the
  published scale/qualification objectives. Other AWS, warehouse, and cross-cloud combinations
  receive no support claim from this proof.
- Provider package extras and the full runtime image do not by themselves qualify Snowflake,
  Redshift, AWS, Azure, or OCI profiles. Snowflake has an experimental adapter with all five writer
  modes behind its provider capability, bounded direct/Parquet paths, temporary remote staging,
  schema checks, destination fencing, fenced table/incremental transforms, and fenced replace-mode
  graph targets. Its disposable-account qualification covered all five modes, direct and Parquet
  paths, transforms, graph execution, replay, stale fencing, concurrent claims, and cleanup. The
  ordinary hosted source runner still selects SCD1, and Snowflake has no views, ARRAY/RECORD
  fallback, measured direct-write crossover, infrastructure provisioning, or synchronous
  total-credit attribution. Same-session query-history enrichment is best-effort and bounded to
  the most recent 1,000 operation IDs per writer or transform session.
  Redshift has an experimental, live-qualified scalar adapter with all five writer
  modes using bounded Parquet parts, same-region S3 manifest `COPY`, IAM roles, replay history,
  destination fencing, and fenced table/incremental transforms. The ordinary hosted source runner
  still selects SCD1. Explicit JSON-to-`SUPER` fields use strict UTF-8 serialization, bounded
  VARBYTE staging, and `JSON_PARSE`; ARRAY/RECORD fallbacks remain unavailable. Replace-mode graph
  targets use the provider-neutral relational AST and fenced table path; graph safe casts fail
  preflight. Its disposable Serverless qualification covered all five modes, direct and Parquet
  paths, transforms, graph execution, replay, stale fencing, concurrent claims, and cleanup.
  Redshift still has no views or provider-managed infrastructure. PostgreSQL state and warehouse
  execution are implemented and locally conformance-tested; all five ingestion modes use bounded
  `COPY` and destination fencing. Replace-mode graph targets use the provider-neutral relational
  AST and fenced table path; graph safe casts and cross-database relations fail preflight. The
  ordinary hosted source runner still selects SCD1. A packaged Helm chart now renders the
  Kubernetes launcher against an existing cluster, but no Kubernetes live profile is qualified
  yet and Dander does not create clusters. PostgreSQL-state/BigQuery-warehouse
  execution remains fail-closed until every BigQuery write mode uses destination-side fencing. The
  package publishes this pair matrix and each warehouse's exact implemented modes, transports,
  schema limits, transforms, graphs, and fencing through `dander runtime compatibility`, while the
  packaged capability manifest remains the support boundary. Local PostgreSQL benchmark results
  are regression evidence, not a paid or controlled-memory scale qualification.
- Azure Container Apps Jobs and Azure Key Vault have a typed projection plus locally validated,
  plan-first Terraform, digest-preserving ACR promotion tooling, provider-native lifecycle
  operations, bounded Log Analytics reads, deployment verification, and a locally validated
  Azure-to-Google managed-identity adapter with disposable federation Terraform and refresh-probe
  tooling. The named Key Vault profile requires an existing delegated Container Apps subnet with
  the `Microsoft.KeyVault` service endpoint; Dander admits that exact subnet and the reviewed
  operator IP while disabling the inapplicable Azure trusted-service bypass. Dander does not
  create or modify the subnet. Azure schedules are
  UTC-only and currently support one replica with 1 CPU/2 GiB or 2 CPU/4 GiB. Resource-provider
  registration, credentialed Azure planning/apply, actual ACR image copy, live identity exchange,
  refresh and revocation,
  live lifecycle execution, and live Snowflake/PostgreSQL acceptance have not yet run, so Azure
  remains unsupported.
- The reserved `oci` extra is empty because Oracle's current SDK requires a `cryptography` version
  below Dander's audited fixed line. OCI implementation must resolve that dependency boundary or
  use a reviewed direct signed-HTTP client before it can enter the full image.

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
- AWS Glue publication is experimental and locally conformance-tested only. It does not provision
  IAM, Glue connections, Lake Formation permissions, crawlers, or tags, and it has not completed a
  live AWS catalog proof. Warehouse-backed entries intentionally have no inferred S3 location.

## Support boundary

`0.7.x` is the current supported beta line. Only its newest patch is supported; a newer minor's
newest candidate may additionally receive acceptance fixes. See [SECURITY.md](../SECURITY.md).
