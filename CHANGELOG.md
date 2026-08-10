# Changelog

Release notes for Dander are kept here and copied into the matching GitHub Release. Dander follows
semantic versioning before 1.0: released minor lines receive fixes only, and new product
capabilities enter through the next minor release.

## Unreleased

## 0.8.0rc8 — 2026-08-10 (beta)

### Fixed

- Preserve EventBridge Scheduler context attributes in the nested Step Functions request,
  allow AWS to generate valid execution names, and permit delivery failures to reach the
  existing exact-scoped dead-letter queue.

## 0.8.0rc7 — 2026-08-09 (beta)

### Fixed

- Recover nonzero `ecs:runTask.sync` task payloads into Dander's existing runtime exit-code
  classifier, while preserving fail-closed handling for genuine Fargate control-plane failures.

## 0.8.0rc6 — 2026-08-09 (beta)

### Fixed

- Normalize the live `ecs:runTask.sync` response from its top-level `TaskArn` and container exit
  code fields so successful Fargate tasks reach Dander's existing exit-code classifier instead of
  failing in Step Functions result selection.

## 0.8.0rc5 — 2026-08-09 (beta)

### Fixed

- Preserve Fargate's configured disk-backed `/tmp` capacity while making the anonymous volume
  writable to Dander's non-root runtime. Generated images now seed mode `1777` volume metadata,
  Fargate uses `/tmp` for home and temporary files, and deployment verification checks the full
  scratch-storage contract.

## 0.8.0rc4 — 2026-08-09 (beta)

### Fixed

- Invoke Step Functions through the AWS CLI's `stepfunctions` service namespace for manual run,
  status, logs, replay, cancellation, and verification. Step Functions ARN and IAM service names
  correctly remain `states`.

## 0.8.0rc3 — 2026-08-09 (beta)

### Fixed

- Replace an invalid wildcard in the Fargate failure-topic resource policy with the explicit
  topic-scoped SNS actions accepted by AWS. This allows a fresh or resumed Fargate platform apply
  to finish without changing runtime behavior or the restricted EventBridge failure publisher.

## 0.8.0rc2 — 2026-08-09 (beta)

### Candidate

- Package renewable Fargate-to-Google workload identity for the complete source-free lifecycle
  acceptance gate. Fargate remains experimental until that gate passes.

### Acceptance

- Record the successful public-`0.8.0rc1` Phase 1B proof: identical GAR/ECR multi-platform
  content, Cloud Run/AMD64 conformance, ARM64 Fargate-to-BigQuery access before and after keyless
  credential refresh, complete proof teardown, and final isolated-platform no-drift.

### Fixed

- Refetch the current ECS task-role session whenever Google Auth refreshes its AWS subject token,
  scope the resulting credential to one OCI invocation, and avoid persisting or globally exporting
  task secrets. The validated Fargate deadline is now 24 hours instead of one hour.
- Make the Phase 1B credential scanner recognize only the exact content hashes of three public
  boto3/botocore example documents containing published placeholder keys. Changed content and all
  other files continue to fail closed.

## 0.8.0rc1 — 2026-08-09 (beta)

### Candidate

- Package the merged cloud-portability foundation, including the keyless Fargate-to-Google
  identity adapter, for isolated Phase 1B acceptance. Fargate, Snowflake, Redshift, Glue, and
  Kubernetes remain experimental until their applicable live gates pass.
- Reject the first Phase 1B image built from public Dander `0.7.0`: that immutable wheel predates
  the merged `dander.identity` package, so the proof exited before credential exchange. The task,
  smoke stack, ECR repository, and rejected GAR image were removed before this candidate.

### Added

- Publish exact write-mode, transport, transform, graph, fencing, logical-type, decimal, and
  temporal limits for every packaged warehouse through `dander runtime compatibility`. Selected
  portable warehouse schema mappers now reject unsupported types and precision before extraction
  or destination mutation without changing BigQuery's provider-native v1 schema behavior.
- Add an experimental AWS Glue Data Catalog provider with canonical relation mapping, direct
  database/table API publication, Dander-owned parameters, unrelated-field preservation, and
  normalized readback. No crawler, deletion, IAM provisioning, or live-support claim is included.
- Extend the experimental Redshift warehouse adapter with whole-DAG-preflighted portable and
  Redshift-authored table/incremental models, strict declared schemas, generic assertions, and
  transactionally fenced DML publication. Views, graphs, other write modes, and support promotion
  remain unavailable.
- Add an experimental Snowflake warehouse adapter with native database/schema coordinates,
  bounded Parquet staging, scalar SCD1 `COPY`/`MERGE`, additive nullable schema evolution,
  destination-side fencing, sanitized telemetry, and process-death-safe temporary staging. This
  is local conformance evidence, not a Snowflake support claim.
- Add a separately packaged AWS Terraform stack for immutable ECR images, non-root Fargate task
  definitions, distinct task/execution roles, paused-aware EventBridge schedules, and encrypted
  failure routing. A Standard Step Functions controller enforces one absolute deadline, retries
  only runtime exit code 75, and keeps scheduler delivery retries separate from launcher attempts.
- Add a lazy AWS Secrets Manager runtime that accepts only full, region-matching secret ARNs,
  retains audited environment indirection, and never loads the AWS SDK until a secret is read.
- Prepare Fargate's keyless Google identity before runtime construction by adapting only temporary
  ECS task-role credentials from the fixed link-local endpoint into a non-secret external-account
  configuration; keep Fargate runs bounded to one hour until renewable credentials are supported.
- Add a lazy ECS/Fargate launcher factory that projects the existing BigQuery data-plane runtime
  onto immutable ECR images, task-role identity, explicit networking, and valid Fargate resource
  pairs without yet claiming deployable Fargate support.
- Select Cloud Run execution-template construction through the lazy launcher-provider registry
  while preserving the existing Terraform projection, resource addresses, and runtime behavior.
- Select GCP Secret Manager and local environment resolvers through the lazy provider registry;
  preserve hosted environment-to-resource-name indirection while preventing environment-only
  secrets from being selected for Cloud Run.
- Select Dataplex or explicit no-catalog behavior through the lazy provider registry; retain
  Dataplex aspect-only publication and readback semantics while avoiding its SDK and credentials
  entirely when catalog publication is disabled.
- Route BigQuery leases, watermarks, run history, and metadata snapshots through one lazily selected
  durable-state runtime with an explicit versioned migration ledger while preserving all existing
  table identities and correctness behavior.
- Route BigQuery writer and transform construction through one lazily selected, typed warehouse
  runtime that also exposes canonical schema, relation rendering, target fencing, normalized job
  telemetry, and explicit capabilities without changing current GCP behavior.
- Expose provider dependency extras for BigQuery, Snowflake, Redshift, PostgreSQL, GCP, AWS, Azure,
  and OCI, plus a validated `runtime-all` union used by repository and generated source-free OCI
  images without expanding the supported-adapter capability manifest.
- Add a provider-neutral run telemetry contract for duration, rows, bytes, retries, provider
  query/job correlation, and explicit decimal cost attribution; emit it on every terminal OCI
  runtime event without admitting arbitrary provider payloads.
- Make durable failure summaries and structured pipeline terminal logs cloud-neutral while
  retaining existing stable failure codes and retry decisions.
- Compile executable graph mappings, joins, transformations, and ordered operations into one
  provider-neutral relational AST before dialect rendering; preserve BigQuery runtime output and
  fail when another target cannot retain graph safe-cast semantics.
- Add explicit `portable`, BigQuery, Snowflake, Redshift, and PostgreSQL model dialect metadata;
  preserve BigQuery as the compatibility default and validate portable models through a closed,
  deterministic SQL AST before provider rendering.
- Add canonical relation/schema v1 contracts with explicit decimal, timestamp, array, record, and
  provider-extension semantics plus a fail-closed BigQuery compatibility mapper.
- Add an API-v1 provider-factory registry for warehouse, state, catalog, secret, and launcher
  categories with strict configuration validation and lazy implementation loading.
- Add version 2 logical projects plus named platform/deployment configuration, deterministic v1
  migration with a read-only compatibility check, and v2 starter projects. The supported hosted
  composition remains GCP/BigQuery/Cloud Run in this change.
- Publish source-free runtime images as one `linux/amd64,linux/arm64` OCI index and reject an
  incomplete registry result before recording publication success.
- Add an isolated, unscheduled Phase 1B proof for digest-preserving GAR-to-ECR copy and keyless
  Fargate-to-BigQuery credential refresh. This is feasibility evidence, not Fargate support.

### Fixed

- Adapt Fargate's short-lived ECS task-role credential endpoint to Google Auth's AWS environment
  chain in-process, and use Google Auth's supported `service_account_impersonation` field so the
  proof token is actually bounded to 600 seconds.
- Point the isolated BigQuery probe defaults at the disposable Salesforce Accounts table used by
  the acceptance project; the probe returns only `COUNT(*)` and never record content.

## 0.7.0 — 2026-08-07 (beta)

### Acceptance

- Promote the accepted `0.7.0rc2` runtime without functional product-code changes.
- Complete source-free local and Cloud Run OCI parity, ServiceNow compatibility, four-endpoint
  Salesforce ingest, transforms/tests/catalog, replay, overlap skip, SIGTERM/SIGKILL recovery,
  cleanup, and final Terraform no-drift.
- Recommend stable Salesforce `0.3.1` and ServiceNow `0.2.2`, which preserve plugin API v1.

## 0.7.0rc2 — 2026-08-07 (beta)

### Fixed

- Keep the running `dander-platform` version in the connector-plugin resolver transaction so an
  incompatible plugin fails clearly instead of silently downgrading a source-free runtime image.
- Recommend the published Salesforce `0.3.1rc1` and ServiceNow `0.2.2rc1` compatibility
  candidates, which preserve plugin API v1 and extend support through Dander `0.7.x`.

### Acceptance

- Reject `0.7.0rc1` after clean source-free installation exposed the plugin-driven runtime
  downgrade; no rc1 image was deployed.

## 0.7.0rc1 — 2026-08-07 (beta)

### Added

- Define the versioned `io.dander.runtime/v1` invocation, event, terminal-result, exit-code, and
  graceful-signal contract without changing the ordinary `dander run` interface.
- Add provider-free runtime inspection and a credential-free local executor/run-ledger/event
  conformance probe.
- Add standard OCI annotations, a packaged capability manifest, attached SBOM/provenance, and an
  atomic artifact record containing immutable index and runnable-platform digests.
- Define immutable `io.dander.execution/v1` templates with separate runtime/launcher retries,
  identity, secret references, resources, schedules, network intent, and observability settings.
- Make Cloud Run consume the shared execution projection and reject unsupported fields before
  planning.

### Compatibility

- Version 1 manifests, GCP/BigQuery behavior, Terraform resource addresses, connector plugin API 1,
  and the ordinary CLI remain compatible.
- This candidate adds no non-GCP provider, warehouse, state backend, catalog, or secret resolver.
- Live source-free local/Cloud Run parity is a promotion gate and is not claimed by this metadata
  commit.

## 0.6.0 — 2026-08-07 (beta)

### Acceptance

- Promote the accepted `0.6.0rc2` runtime without functional product-code changes.
- Complete source-free hosted Salesforce ingestion for Accounts, Contacts, Opportunities, and
  Users, governed transforms and tests, inclusive replay, visible soft-deletion tombstones,
  released leases, staging cleanup, Dataplex publication, and Terraform no-drift.
- Confirm the stable Salesforce `0.3.0` and ServiceNow `0.2.1` plugin packages install together
  outside their repositories and preserve plugin API version 1.

### Classification

- Move Dander's public package and documentation from Alpha to Beta while retaining the explicit
  pre-1.0 limitations and newest-patch support policy.

## 0.6.0rc2 — 2026-08-07 (alpha)

### Fixed

- Preserve BigQuery's Google-managed required schema aspect during Dataplex publication while
  continuing to publish Dander's optional overview, contacts, and generic metadata aspects.

## 0.6.0rc1 — 2026-08-06 (alpha)

### Added

- Add sanitized failure codes and summaries to local and hosted run history, and reconcile stale
  non-terminal runs after an expired lease is safely reacquired.
- Give every BigQuery run-scoped staging table a 24-hour expiration at creation while retaining
  immediate cleanup for handled completion and failure paths.
- Add plan-first stage-zero and platform commands, source-free immutable image publication, a
  read-only permission preflight, and documented least-privilege upgrade and rollback workflows.
- Expand the Salesforce project contract to four independently watermarked CRM endpoints, four
  governed staging models, and an Opportunity fact with Account and historical owner dimensions.

### Compatibility

- Existing manifests, pipeline IDs, built-in connectors, and plugin API version 1 remain valid.
- Salesforce remains read-only; hard-delete discovery, custom fields without declared schemas,
  additional clouds, and non-BigQuery destinations remain outside this candidate.

## 0.5.1 — 2026-08-05 (alpha)

### Fixed

- Update the curated Salesforce and ServiceNow connector recommendations to their published
  `0.2.0` releases and accurately require Dander `0.5.x`.

## 0.5.0 — 2026-08-05 (alpha)

### Added

- Add read-only connector capability discovery, connection checks, exact counts, and targeted
  record lookup without changing the existing source interface.
- Add a connector scaffold, API-v1 conformance helpers, and a curated PyPI-backed plugin catalog.
- Host Druff beside Dander's operator-bound graph service and publish presentation-safe connector,
  operation, and catalog descriptors.
- Execute ordered, schema-preserving trim, truncate, default, and bounded-filter operations from
  canonical graphs after raw ingestion.

### Acceptance

- Public candidates completed a source-free isolated proof with Druff-authored graph operations,
  Salesforce connection/count/lookup capabilities, ingestion, replay, monotonic cursor, released
  lease, staging cleanup, restored schedules, and Terraform no-drift.
- Stable `0.5.0` preserves the accepted `0.5.0rc2` runtime behavior.

## 0.5.0rc2 — 2026-08-05 (alpha)

### Added

- Add ordered, schema-preserving trim, truncate, default, and bounded-filter operations to
  canonical transform nodes and execute them in `dander run` after raw ingestion.
- Publish presentation-safe operation descriptors at `GET /v1/operations` for canonical Druff
  authoring without creating a second runtime contract.

## 0.5.0rc1 — 2026-08-05 (alpha)

### Added

- Add an atomic `dander plugins scaffold` command that creates a generic REST connector package,
  API-v1 conformance test, example declaration, and inert CI/trusted-publishing workflows.
- Expose reusable plugin declaration, installed-entry-point, and optional source-factory checks.
- Document the smallest supported path from scaffold through simulator and real-account acceptance.
- Add a small curated catalog for the provider-validated Salesforce and ServiceNow connector
  packages, including exact pins, Dander compatibility, support status, and public links.
- Add `dander plugins search` and a presentation-safe `GET /v1/plugin-catalog` graph-service route
  whose installed markers come only from validated manifest declarations.
- Add optional Terraform hosting for Druff beside Dander's operator-bound graph service.
- Add a read-only source capability contract for exact record lookup, count, and non-record
  connection checks without changing the existing `Source` interface.
- Add `dander connector inspect` and `dander connector check` so operators can discover and invoke
  those optional capabilities through manifest-pinned connector plugins.

### Compatibility

- Existing sources and plugins remain valid without implementing optional capabilities.
- This candidate adds no provider write-back, deleted-record feed, or raw-ingestion transformation
  path.

## 0.4.0 — 2026-08-04 (alpha)

### Added

- Add exactly pinned connector plugins discovered through standard Python entry points and installed
  into generated source-free images from the project manifest.
- Publish presentation-safe installed-connector descriptors for Druff without exposing connector
  URLs, authentication settings, secret references, request bodies, or credentials.

### Fixed

- Keep atomically saved project files readable by the generated image's unprivileged runtime user.
- Verify unguarded deployments without requiring cost-guard billing or Pub/Sub roles, while
  preserving the guarded deployment IAM checks.

### Acceptance

- Public Dander `0.4.0rc3` with Salesforce plugin `0.1.0rc1` completed initial ingestion, canonical
  graph save and execution, replay, and controlled overlap in a fresh disposable GCP project.
  Salesforce remained at 14 unique Accounts, the cursor stayed monotonic, one overlapping run
  skipped, the lease released, run-scoped staging cleared, and both Terraform plans were clean.
- Public `0.4.0rc4` installed outside the checkout, generated and validated a source-free project,
  passed generated Terraform validation, and passed every live deployment-verifier check against
  the isolated paused deployment.
- The final `0.4.0` runtime and Terraform behavior are unchanged from the accepted `0.4.0rc4`.

## 0.4.0rc4 — 2026-08-04 (alpha)

### Fixed

- Make deployment verification match the existing unguarded installation contract: ordinary
  runtimes no longer require cost-guard billing or Pub/Sub roles, while guarded deployments retain
  their existing IAM checks.

## 0.4.0rc3 — 2026-08-04 (alpha)

### Fixed

- Make generated source-free images own their copied manifest, connector, graph, and model files as
  the unprivileged runtime user. This keeps a graph saved atomically with owner-only permissions
  readable inside the container without weakening its host filesystem permissions.

## 0.4.0rc2 — 2026-08-04 (alpha)

### Added

- Add presentation-safe connector discovery at `GET /v1/connectors` for the operator-bound graph
  service. The response contains graph bindings, declared fields, and exact plugin package identity,
  but no URLs, authentication settings, secret references, request bodies, or credentials.
- Prepare Dander to serve the independently packaged Salesforce connector descriptor while keeping
  the existing built-in `salesforce_bulk2` adapter as a deprecated fallback.

### Compatibility

- Static/offline Druff use and projects without plugins remain unchanged.
- Connector discovery is additive; graph open/save, execution, and deployment boundaries are
  unchanged from `0.4.0rc1`.

## 0.4.0rc1 — 2026-08-04 (alpha)

### Added

- Add manifest-pinned connector plugins discovered only through the `dander.connectors` Python
  entry-point group, with a public API v1 contract and strict package/version compatibility checks.
- Add `dander plugins install` and invoke it from generated source-free images so independently
  distributed connectors can be installed without copying Dander source.

### Compatibility

- Existing manifests without `plugins` and all built-in ingestion engines continue unchanged.
- Explicitly declared plugins may replace a built-in engine; unrelated duplicate plugin engines
  and undeclared installed plugins remain inactive.

## 0.3.0 — 2026-08-04 (alpha)

### Added

- Add isolated, source-free deployment previews and explicit deployed-graph operations for Druff's
  canonical PipelineGraph workflow.
- Add a real-instance-proven Odoo 19+ JSON-2 partner slice with declared schema, bounded paging,
  inclusive watermark replay, transform tests, and metadata.
- Add a stateful NetSuite SuiteQL simulator and customer slice covering OAuth1 TBA, stable paging,
  throttling, permissions, malformed responses, and replay. This remains simulator-validated and
  is not represented as real-tenant NetSuite support.

### Changed

- Move Salesforce Accounts extraction to Bulk API 2.0 QueryAll with server-filtered SOQL, bounded
  job polling, streamed locator pages, cleanup, and soft-delete visibility.
- Include ServiceNow in the retained daily operator soak without changing its existing connector
  contract.

### Acceptance

- Merged Odoo installed source-free and completed initial ingestion, transform/test, metadata, and
  replay against an ephemeral official Odoo 19 instance; raw and staging stayed at five unique
  partner IDs and the lease cleared.
- Salesforce installed source-free and completed initial load, tied-boundary replay, one-record
  server-filtered replay, and soft-delete capture against the disposable dev org; raw and staging
  stayed at 14 unique Account IDs, the cursor advanced monotonically, and the lease cleared.
- Protected CI passed Python quality, Terraform and static security validation, distribution
  installation, container scanning, and secret scanning on both accepted capability PRs.
- Public `0.3.0rc1` installed outside the checkout, generated and validated a source-free project,
  built that project's Docker image, and started the packaged CLI successfully.
- The final `0.3.0` runtime and Terraform behavior are unchanged from the accepted candidate.

## 0.2.0 — 2026-08-04 (alpha)

### Added

- Add read-only Salesforce Accounts and ServiceNow incidents connectors, including typed
  authentication, declared schemas, simulators, transforms, tests, and hosted pipeline definitions.
- Add stateful Workday and ServiceNow simulators with realistic pagination and named authentication,
  throttling, permission, and malformed-record scenarios.
- Add strict local PipelineGraph persistence for Druff with explicit open/save, revision conflict
  protection, atomic replacement, and lossless canonical graph validation.
- Add the first connector-backed PipelineGraph runtime and a separate paused Greenhouse graph job.

### Acceptance

- The public `0.2.0rc5` package installed source-free and its generated image ran the retained
  Greenhouse graph successfully twice.
- Both graph executions published 21 unique rows matching the raw source exactly, recorded complete
  run-ledger entries, released their leases, and removed completed staging.
- Final stage-zero and platform Terraform plans reported no changes. The `0.2.0` runtime is unchanged
  from the accepted candidate.

## 0.2.0rc5 — 2026-08-03 (alpha)

### Added

- Add strict local PipelineGraph persistence for Druff with explicit open/save, revision conflict
  protection, atomic replacement, and lossless canonical graph validation.
- Add the first connector-backed PipelineGraph runtime: bind existing connector endpoints, execute
  inside Dander's run-history and lease lifecycle, and publish replace targets through expiring
  staging plus a transactionally fenced BigQuery finalizer.
- Package an inactive Greenhouse graph example and add a separate paused retained-project job for
  source-free hosted acceptance without changing existing pipeline definitions.

## 0.2.0rc4 — 2026-08-03 (alpha)

### Added

- Add a real-instance-proven, read-only ServiceNow incidents connector using OAuth client
  credentials, declared raw fields, primitive internal values, and stable full-read pagination.
- Add a stateful ServiceNow simulator, realistic multi-page fixtures, a narrow OpenAPI contract,
  named failure scenarios, a staging model, and a paused hosted pipeline definition.

## 0.2.0rc3 — 2026-08-03 (alpha)

### Fixed

- Canonicalize valid timezone-aware declared timestamps before JSON loading so provider offsets
  such as Salesforce's `+0000` form reach BigQuery as typed values with `+00:00` offsets.
- Store timestamp watermarks in canonical ISO 8601 form after normalization.

## 0.2.0rc2 — 2026-08-02 (alpha)

### Fixed

- Encode validated decimal and temporal values for BigQuery JSON load jobs, including values in
  nested records and repeated fields, without changing source or schema typing.

## 0.2.0rc1 — 2026-08-02 (alpha)

### Added

- Add a complete read-only Salesforce Accounts slice using External Client App JWT authentication,
  QueryAll response-link pagination, declared raw schema, idempotent SCD1 publication, transforms,
  tests, and governed metadata.
- Add a stateful Workday RaaS simulator with realistic pagination and named authentication,
  throttling, permission, and malformed-record scenarios.

### Changed

- Allow OAuth2 JWT connectors to declare a provider-specific authorization-server audience and
  assertion lifetime while preserving existing provider defaults.
- Support opaque next-page URLs carried in JSON response bodies through the shared declarative REST
  connector contract.

## 0.1.1 — 2026-08-02 (alpha)

### Fixed

- Make newly generated hosted projects default to the existing unguarded installation path, so
  standard provisioning needs no billing-account ID or billing-account IAM changes.
- Omit the managed cost guard, guard-specific IAM, and guarded runtime preflight when that existing
  safety setting is disabled, while preserving the opt-in guarded path.

### Acceptance

- Public `0.1.1rc1` installed source-free in a fresh billing-linked project using an operator
  identity with no billing-account IAM role.
- The hosted Greenhouse run ingested 21 rows, built one model, passed three assertions, recorded a
  successful run, enabled its daily schedule, and finished with a no-change Terraform plan.
- The `0.1.1` runtime is unchanged from the accepted candidate.

## 0.1.1rc1 — 2026-08-02

### Fixed

- Make newly generated hosted projects default to the existing unguarded installation path, so
  standard provisioning needs no billing-account ID or billing-account IAM changes.
- Omit the managed cost guard, guard-specific IAM, and guarded runtime preflight when that existing
  safety setting is disabled, while preserving the opt-in guarded path.

## 0.1.0 — 2026-08-02 (alpha)

### Added

- Installable `dander-platform` package and source-free generated projects; imports and the CLI
  remain `dander`.
- Bounded hosted ingestion, declared schemas, concurrency fencing, cursor compare-and-set, durable
  run history, and failure alerting.
- Greenhouse and HubSpot hosted pipelines with transforms, tests, metadata, and replay-safe writes.
- Hosted Greenhouse quickstart, upgrade runbook, security policy, supported-version statement, and
  consolidated known limitations.

### Changed

- Classify the public product and package metadata as alpha.
- Correct retained-project documentation to reflect that both hosted schedules are enabled.

### Acceptance

- Public `0.1.0rc7` installed and generated a validated source-free project outside the checkout.
- A fresh disposable project completed the independent source-free Greenhouse installation gate.
- The retained project passed simultaneous Greenhouse/HubSpot execution, Greenhouse replay,
  HubSpot create/update/replay/cleanup, same-pipeline overlap, cursor, lease, staging, alert, and
  duplicate checks.
- Both schedules were restored to the tracked enabled state and the final Terraform plan reported
  no changes. The post-release operator soak does not block this release.

## 0.1.0rc7 — 2026-08-02

### Fixed

- Isolate hosted pipeline leases in deterministic per-pipeline BigQuery tables so unrelated
  finalizers and heartbeats cannot contend on one shared table.
- Retry BigQuery's alternate concurrent-update serialization message while preserving the same
  bounded retry policy.

## 0.1.0rc6 — 2026-08-02

### Fixed

- Retry BigQuery's transaction-aborted concurrent-update response with a bounded exact-error
  policy.

## 0.1.0rc5 — 2026-08-02

### Fixed

- Report the missing immutable image for plan-only `dander init` as a normal CLI usage error
  instead of exposing a Python traceback.

## 0.1.0rc4 — 2026-08-02

### Fixed

- Wait for new-project Cloud Functions service identities and cost-guard IAM grants to propagate
  before starting the first function build.

## 0.1.0rc3 — 2026-08-02

### Fixed

- Wait for a newly granted stage-zero service-account impersonation role to become usable before
  starting platform Terraform, avoiding a first-run IAM propagation race.
- Set the Application Default Credentials quota project explicitly in the hosted quickstart.

## 0.1.0rc2 — 2026-08-02

### Fixed

- Keep read-only, full-extraction watermarks monotonic when records newer than the current source
  population have been deleted between runs.
- Render in-progress executions in `dander metadata runs` instead of rejecting the persisted
  `running` lifecycle state.

## 0.1.0rc1 — 2026-08-01

### Added

- Installable `dander-platform` wheel and source distribution; imports and the CLI remain `dander`.
- `dander new` with a paused, credential-free Greenhouse project, source-free runtime Dockerfile,
  and complete Terraform modules.
- Hosted SCD1 and sandbox-replace bounded ingestion, declared raw schemas, empty-source bootstrap,
  top-level nullable schema evolution, exclusive leases, fencing tokens, and cursor compare-and-set.
- Additive Greenhouse and HubSpot hosted pipelines with independent identities, schedules, secrets,
  transforms, tests, metadata snapshots, durable run history, and failure alerting.
- Exact-tag, environment-approved PyPI trusted publishing.

### Known limitations

This candidate is alpha and subject to the documented
[known limitations](docs/known-limitations.md). It is not represented as production-ready.
