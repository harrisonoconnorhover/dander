# Known limitations

Dander `0.9.x` is beta and proves a focused GCP-native vertical slice. It remains pre-1.0 software;
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
  tooling. The named Azure/Snowflake/PostgreSQL/no-catalog/Key-Vault profile passed live manual and
  UTC-scheduled execution, replay, overlap fencing, interruption, retry exhaustion, alert routing,
  versionless secret rotation, immutable rollback, cleanup, and retained-GCP no drift. The
  Azure-to-Google profile separately passed live BigQuery access across credential refresh, GCP
  Secret Manager access, Dataplex read-back, revocation, and cleanup without a credential file or
  long-lived cloud key. The local `dander azure status` command does not yet accept the explicit GCP
  project needed to reconstruct that experimental cross-cloud deployment; it therefore fails
  closed, and Azure-native status was used for the proof. The named Snowflake profile is unaffected.
  A separate read-only preflight rejects any composition other than the named
  Azure/Snowflake/PostgreSQL/no-catalog/Key-Vault profile and verifies only declared secret names
  and enabled state. The named Key Vault profile requires an existing delegated Container Apps
  subnet with the `Microsoft.KeyVault` service endpoint; Dander admits that exact subnet and the
  reviewed operator IP while disabling the inapplicable Azure trusted-service bypass. Dander does
  not create or modify the subnet. Azure schedules are
  UTC-only and currently support one replica with 1 CPU/2 GiB or 2 CPU/4 GiB. Resource-provider
  registration and disposable network creation remain explicit operator-approved actions. These
  bounded proofs close Phase 6 but do not supply Phase 8 scale, throughput, cost, soak,
  pairwise-profile, or release qualification, so Azure remains experimental rather than supported.
- The `oci` extra now installs Oracle's SDK `2.184.1` or newer without downgrading Dander's audited
  `cryptography` line. OCI Vault has a lazy, resource-principal-only resolver for exact secret OCIDs
  and versionless vault/name references. OCI Container Instances now has a typed, lazy execution
  projection for only the PostgreSQL/PostgreSQL/no-catalog/OCI-Vault profile. It requires an exact
  digest in the selected OCIR repository, a resource-principal dynamic group, UTC five-field cron,
  whole-GiB memory, and one container attempt with provider restart policy `NEVER`; unsupported
  profiles and inputs fail before OCI access. OCI OCPUs are provider billing/resource units rather
  than a cross-provider performance claim, and the fixed 15 GB ephemeral-storage allocation is not
  configurable through this projection. A resource-principal OCI Function now owns deterministic
  run identity, maximum parallelism one, whole-task retry only for exit code 75, bounded logs,
  stop/delete cleanup, immutable terminal history, Resource Scheduler delivery, and lifecycle-event
  reconciliation. The Function's one-hour detached limit leaves a five-minute cleanup reserve, so
  OCI tasks are capped at 3,300 seconds. Resource Scheduler is UTC-only and cannot schedule an
  interval shorter than one hour. OCI Vault references resolve the `CURRENT` version at the start
  of every runtime process and are removed from the process environment afterward; in-process
  rotation is not claimed. The controller image is a Python 3.12 `GENERIC_X86` image built from an
  exact wheel, not the source-free task image. Digest-preserving OCIR promotion is implemented with
  an ephemeral repository-scoped token, but live publication, provider deployment verification,
  credential-rotation proof, and the complete OCI profile remain unqualified. No OCI launcher
  support claim exists until those separate gates pass.

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
