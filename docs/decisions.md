# Engineering Decisions

## 2026-08-10 — Fargate lifecycle evidence does not skip profile qualification

- **Accepted slice:** Public `0.8.0rc8` passed manual and scheduled execution, replay,
  interruption, alert routing, image rollback, cleanup, and no-drift for the named
  Fargate-to-BigQuery/GCP composition.
- **Support boundary:** Fargate remains experimental until the same profile satisfies the
  published scale and qualification objectives. No other AWS, warehouse, or cross-cloud pairing
  inherits this evidence.
- **Evidence:** The bounded record is
  `docs/cloud-portability-fargate-lifecycle-acceptance.md`.

## 2026-08-09 — Phase 1B accepts only exact public dependency fixtures

- **Evidence:** The accepted image contains three boto3/botocore example files with AWS-published
  placeholder keys; neither architecture contains a cloud credential.
- **Control:** The proof scanner recognizes only those files' exact SHA-256 content. A dependency
  update or modified file loses the exception and is scanned normally.
- **Boundary:** This changes acceptance evidence tooling only. It does not alter the published
  `0.8.0rc1` runtime or weaken scanning of configuration, state, task output, logs, or other image
  content.

## 2026-08-09 — Permission checks follow the resource that owns the permission

- **Project:** Stage zero tests project-scoped create and IAM permissions through Resource Manager.
- **Bucket:** An existing state bucket's get/update permissions are tested through Cloud Storage's
  bucket-specific endpoint; a 404 leaves creation and its authoritative checks to Terraform.
- **Scope:** Billing-account and Workload Identity permission checks remain unchanged.

## 2026-08-09 — Phase 1B proof identities are repeatable after teardown

- **Naming:** The disposable smoke root accepts one validated `proof_name`; its default preserves
  the original AWS, log, Google Workload Identity, and service-account identities.
- **Reruns:** Operators select a new deterministic name when Google's soft-delete window prevents
  immediate reuse of a destroyed Workload Identity Pool ID.
- **Boundary:** A new name creates an independent proof. Dander does not undelete, import, or adopt
  remnants from an earlier proof automatically.

## 2026-08-08 — Fargate operations bind to the manifest and controller

- **Ownership:** The validated deployment and pipeline determine the exact state machine, schedule,
  task definition, log group, and ECR image checks; operators do not supply arbitrary AWS resources.
- **Lifecycle:** Step Functions remains authoritative for start, status, cancellation, and replay;
  CloudWatch events are correlated through the execution's exact ECS task identifier.
- **Output:** Commands expose small normalized records and scoped errors, not unrestricted AWS
  responses. This completes the operator surface but does not claim support before live acceptance.

## 2026-08-08 — AWS stage zero owns state and artifact prerequisites

- **Ownership:** A separate AWS stage-zero root owns the customer-key-encrypted S3 backend,
  DynamoDB lock table, immutable ECR repository, and dedicated deployment role. The Fargate
  platform root consumes the repository instead of attempting to create it after image promotion.
- **Lifecycle:** The first reviewed stage-zero plan uses secured local operator state. Applying
  that exact plan creates the remote backend and immediately migrates state into S3; a failed
  migration preserves the local recovery copy.
- **Artifact:** AWS publication copies the accepted source-free OCI index without rebuilding and
  fails unless the index and every platform digest remain identical in ECR.

## 2026-08-08 — Fargate lifecycle is provider-native and bounded

- **Controller:** One Standard Step Functions state machine per pipeline uses the optimized ECS
  `.sync` integration. Its workflow timeout bounds all whole-runtime attempts and AWS owns
  best-effort `StopTask` behavior on timeout or cancellation.
- **Retries:** Exit code 75 is the only runtime result eligible for a launcher retry. EventBridge
  delivery retries remain a distinct counter and both exhausted paths reach an encrypted queue.
- **Boundary:** The AWS stack is packaged separately with native S3 backend configuration and
  remains outside the public support manifest until CLI lifecycle and live parity are proven.

## 2026-08-08 — Fargate identity remains keyless and bounded

- **Identity:** Fargate accepts only temporary `ASIA` task-role credentials from the fixed ECS
  link-local endpoint. Preconfigured AWS access keys, unsafe endpoints, and nearly expired sessions
  fail before provider clients are constructed.
- **Google federation:** Dander writes a non-secret external-account configuration with a 600-second
  impersonated token lifetime; task credentials remain in process memory and are never logged.
- **Boundary:** Fargate deadlines are capped at one hour until renewable ECS credential supply and
  launcher lifecycle are delivered. This remains construction readiness, not a support claim.

## 2026-08-08 — Fargate projection precedes infrastructure support

- **Boundary:** The lazy Fargate factory projects the existing BigQuery/GCP runtime onto an
  immutable ECR image, AWS task role, `awsvpc` placement, and declared CloudWatch destinations.
- **Fail closed:** Unsupported CPU/memory pairs, guarded-free-tier execution, mutable images, and
  invalid account/network identifiers fail before any infrastructure operation.
- **Support:** Factory registration is not a support claim. Terraform, controller lifecycle,
  keyless identity, and live comparison remain separate promotion gates.

## 2026-08-08 — Cloud Run construction uses the launcher provider boundary

- **Selection:** Version 1 and migrated version 2 projects retain `cloud_run`; Terraform bootstrap
  builds its launcher runtime through the shared lazy API-v1 provider registry.
- **Parity:** The Cloud Run factory delegates to the accepted execution projector, preserving
  template values, Terraform addresses, schedules, IAM, alerts, and runtime behavior.
- **Scope:** This change creates a construction seam only. Fargate lifecycle and infrastructure
  remain a separate vertical slice with their own planning and acceptance.

## 2026-08-08 — Secret resolution uses an explicit provider runtime

- **Compatibility:** Version 1 and the Cloud Run profile retain GCP Secret Manager plus the
  existing environment-variable indirection; secret names, resource paths, and audit events do
  not change.
- **Selection:** Hosted and connector-capability paths build the manifest-selected API-v1 secret
  runtime; sandbox execution explicitly selects the environment provider.
- **Boundary:** Environment-only secrets remain limited to local or future operator-managed
  Kubernetes execution, and the Google Secret Manager SDK loads only on actual GCP access.

## 2026-08-08 — External catalog publication is selected independently

- **Selection:** Version 1 retains implicit Dataplex behavior; version 2 carries `dataplex` or
  `none` into the API-v1 catalog factory while explicit legacy CLI publication still selects
  Dataplex.
- **Dataplex parity:** The existing first-party BigQuery entry naming, optional aspect-only updates,
  unrelated-field preservation, required-schema exclusion, and normalized readback remain intact.
- **No catalog:** The selected runtime has no publisher and imports no Dataplex implementation;
  local semantic manifests and durable metadata snapshots remain separate, unchanged stages.

## 2026-08-08 — Durable state is composed without relocating existing data

- **Selection:** Version 1 projects retain implicit BigQuery state; version 2 resolution carries
  the named state provider into the same lazy API-v1 factory registry as the warehouse.
- **Migration:** One `_dander_state_schema` ledger records completed provider schema versions only
  after the existing watermark, history, and catalog tables are ready. Re-entry is idempotent.
- **Compatibility:** Existing table identities, per-pipeline lease tables, server-time leases,
  fencing, watermark CAS, and interrupted-run reconciliation remain unchanged.

## 2026-08-08 — PostgreSQL is the second durable-state implementation

- **Connection boundary:** Version 2 profiles store only an environment-variable name; the
  PostgreSQL connection string is runtime-injected and never serialized into project manifests.
- **Correctness:** PostgreSQL 15+ uses one bounded pool, short transactions, server-time leases,
  monotonic fencing tokens, atomic cursor CAS, sanitized history, deterministic JSONB snapshots,
  and an advisory-locked version ledger that rejects newer schemas.
- **Qualification:** The adapter passes live local contention and exhaustion tests, but it does
  not make a PostgreSQL/Kubernetes profile supported before warehouse and live-profile gates pass.

## 2026-08-08 — BigQuery enters portability through one composed runtime

- **Selection:** Version 1 projects retain an implicit BigQuery warehouse; version 2 resolution
  carries the selected provider and location into execution.
- **Composition:** One lazily built `WarehouseRuntime` owns small relation, schema, writer,
  transform, fence, telemetry, and capability surfaces; the CLI does not branch on BigQuery.
- **Scope:** Existing BigQuery classes remain behaviorally intact. State, catalog, secrets, and
  launchers move through their own focused Phase 3 changes.

## 2026-08-07 — Provider dependencies are assembled separately from support claims

- **Local installs:** Public extras group BigQuery, Snowflake, Redshift, PostgreSQL, GCP, AWS, and
  Azure SDK dependencies without importing or registering an adapter. The OCI name is reserved but
  empty while Oracle's SDK requires a cryptography version with a known fixed-in-50 advisory.
- **Release image:** `runtime-all` is their checked union; repository and generated Dockerfiles
  install and validate it so one immutable multi-platform image has deterministic dependencies.
  Linux uses pure-Python Psycopg with Debian's maintained `libpq5`, avoiding a bundled wheel whose
  embedded native-library SBOM fails the release image scan. Non-Linux extras use Psycopg's binary
  package so local provider installation does not require a separately linked `libpq`.
- **Support boundary:** `runtime-capabilities.json`, concrete adapter conformance, and live profile
  gates remain authoritative. A present SDK or package extra never makes a provider supported.

## 2026-08-07 — Runtime telemetry is normalized before provider reporting

- **Contract:** Terminal runtime events carry one validated `RunTelemetry` shape with whole-run
  duration and ordered row, byte, retry, query/job, and monetary-cost operation statistics.
- **Safety:** The closed payload admits only bounded identifiers and numeric aggregates; arbitrary
  provider metadata, SQL, URLs, request bodies, rows, and exception strings are not telemetry.
- **Scope:** Core currently measures elapsed time. Concrete warehouse adapters populate detailed
  operations in their own vertical slices, and zero values do not imply provider billing data.

## 2026-08-07 — Graph nodes compile to relational expressions before SQL

- **Boundary:** Source relations, CTEs, joins, projections, scalar transforms, and ordered graph
  operations are sqlglot expressions. Graph nodes no longer concatenate BigQuery SQL fragments.
- **Compatibility:** `CompiledTarget.query` remains the BigQuery rendering and the existing
  BigQuery writer path is unchanged. Its AST is exposed only as an isolated copy.
- **Fail closed:** Other dialects render only when their semantics are exact. Current safe-cast
  nodes reject Snowflake and PostgreSQL instead of silently becoming strict casts; rendering alone
  does not claim executable provider support.

## 2026-08-07 — Portable SQL is an explicit, closed contract

- **Compatibility:** Model metadata defaults to exact `bigquery`; repository-owned legacy models
  state it explicitly. Exact SQL never translates to another provider.
- **Portability:** `portable` models use only declared `ref()` relations and a closed sqlglot AST.
  Dander rejects unknown nodes, implicit null ordering, unstable windows, non-canonical identifiers
  and literals, lossy casts, and provider-specific constructs before rendering.
- **Scope:** BigQuery, Snowflake, Redshift, and PostgreSQL are render targets, not support claims.
  Provider variants, graph AST reuse, assertions, materializations, and live equivalence remain
  independent tickets.

## 2026-08-07 — Canonical schemas preserve semantics before rendering

- **Relations:** Internal coordinates use `RelationRef(catalog, namespace, name)`. Only a selected
  provider codec may validate provider limits, quote identifiers, or render SQL.
- **Types:** Schema contract v1 distinguishes numeric bit widths, decimal precision/scale,
  timestamp timezone/precision semantics, arrays, records, and nullable/required cardinality.
  Provider annotations are ordered extensions and never credentials.
- **Compatibility:** Existing BigQuery `RawField` and `WriteField` declarations map one way into the
  canonical contract. Unsupported types fail unless the caller supplies an explicit fallback;
  authored BigQuery files and runtime behavior remain unchanged in this slice.

## 2026-08-07 — Provider factories are explicit and lazy

- **Selection:** Warehouse, state, catalog, secret, and launcher factories use one API-v1 registry
  keyed by category and validated provider ID. Duplicate and unknown registrations fail clearly.
- **Loading:** Configuration models remain lightweight. A provider implementation and its SDKs are
  imported only when that exact provider is built; merely validating another profile does not load
  unselected providers.
- **Boundary:** This registry establishes construction and compatibility rules only. A provider is
  not supported until its concrete adapter, conformance suite, and live profile pass separately.

## 2026-08-07 — Version 2 separates logical projects from named deployments

- **Configuration:** One logical project may be resolved through multiple named platform profiles.
  `dander.yaml` version 2 owns provider-neutral pipeline intent; `dander.platforms.yaml` owns
  provider selections and launcher projection. Multiple deployments require explicit selection.
- **Compatibility:** Version 1 remains loadable. `dander config migrate --check` must prove the
  deterministic v2 split resolves to identical current GCP settings, pipeline behavior, stable
  resource names, and Terraform projection before either file is written.
- **Provider direction:** The release image will contain all first-party adapters while local Python
  installs may use extras. PostgreSQL 15+ is the minimum. Dataplex and Glue may represent
  non-native warehouses. OCI uses Container Instances plus a narrow launch Function; OKE remains
  the Kubernetes path. Qualification uses `docs/cloud-portability-slos.md`, and paid testing has a
  zero-dollar default until a separate per-proof ceiling is approved.

## 2026-08-07 — Cloud Run consumes the shared execution projection

- **Decision:** The GCP plan compiler passes `io.dander.execution/v1` templates into Terraform;
  Cloud Run no longer reconstructs runtime commands, limits, schedules, environment, or secret
  references independently.
- **Compatibility:** Version 1 manifests and existing Terraform resource addresses remain stable.
  The runtime command now emits the versioned JSON event contract while preserving ingestion,
  selected transforms/tests, registry output, Dataplex publication, safety, and batch settings.
- **Fail closed:** Terraform validates the provider/profile and rejects unsupported networking,
  ephemeral-storage, task, parallelism, retry, identity, secret, or observability projections.

## 2026-08-07 — Launchers consume a validated execution projection

- **Decision:** Compile hosted intent into immutable `io.dander.execution/v1` templates containing
  runtime, identity, resource, scheduling, networking, and observability fields. Bind run-specific
  correlation only when an execution starts.
- **Validation:** Each launcher declares exact capabilities and rejects unsupported fields before
  planning; requested values are never silently ignored.
- **Compatibility:** Version 1 manifests compile to the existing GCP profile. Cloud Run initially
  advertises only the behavior Dander already provisions, including one task and one parallel worker.

## 2026-08-07 — Cloud-selectable direction preserves a named GCP compatibility profile

- Portability is introduced through a versioned OCI runtime contract and named, validated platform
  profiles; logical pipelines do not gain provider conditionals.
- GCP/Cloud Run/BigQuery/Dataplex/GCP Secret Manager remains the primary compatibility profile.
  Existing version 1 manifests and resource addresses remain compatible while the new contracts
  are proven incrementally.
- A launcher, warehouse, state, catalog, or secret combination is unsupported until the exact
  combination passes its conformance, identity, failure, and live-profile gates. Interfaces and
  simulators alone do not create a support claim.

## 2026-08-04 — Graph deployment preview is artifact-writing but non-applyable

- Graph Save remains a local conditional file write. Candidate creation is a separate explicit
  action that pushes one source-free Artifact Registry image for the exact saved graph revision.
- The operator fixes every Terraform input when starting the loopback service. Druff supplies only
  the ETag and receives the sensitivity-aware human plan plus shared-image job names.
- Terraform runs against an isolated temporary infrastructure copy; its binary plan is deleted and
  cannot enter the normal apply path. Deployment and scheduler control remain separate work.

## 2026-08-03 — ServiceNow v1 favors complete reads over unsafe cursor paging

- The first ServiceNow slice uses OAuth client credentials and the read-only Table API incident
  operation; simulator mutations exist only to prepare deterministic acceptance state.
- Extraction requests primitive internal values and orders every full read by
  `(sys_updated_on, sys_id)`. It does not combine a timestamp watermark with offset paging,
  because records moving between pages could be skipped.
- The hosted pipeline is additive and paused by default. Source hard deletes are not propagated;
  incrementality requires a later keyset-pagination contract proven against a real instance.

## 2026-08-02 — Simulate the first Workday tenant contract before live acceptance

- The first read-only Workday slice is three provider operations: tenant token issuance plus
  `Dander_Workers` and `Dander_Organizations` RaaS custom reports. Simulator controls are never
  represented as Workday APIs.
- The local FastAPI service uses invented packaged fixtures, deterministic state changes, and
  named failures to exercise Dander over loopback without credentials, GCP, or tenant data.
- Tenant-specific OAuth grant support, report prompt aliases, and domain permissions remain
  explicit real-tenant acceptance items; passing the simulator is not called tenant validation.

## 2026-08-01 — Alpha stabilization and latest-patch support

- Dander `0.1.x` accepts only defects a user can encounter in installation, upgrades,
  infrastructure reconciliation, ingestion correctness, cleanup, CLI output, security, or
  blocking documentation. New connectors and capabilities wait for `0.2.0`.
- The public product is explicitly alpha. Only the newest patch of the current `0.x` minor is
  supported; a superseded patch or candidate receives no backports.
- Candidate acceptance uses the public distribution from a source-free project. A packaged
  runtime fix invalidates the candidate and restarts acceptance; documentation-only corrections
  do not.

## 2026-08-01 — Installable distribution and complete starter project

- PyPI distribution `dander-platform` avoids the occupied `dander` project name; the public Python
  package and CLI remain `dander`, and version metadata has one installed-distribution source.
- `dander new DIR` atomically copies a paused, credential-free project plus the tracked Terraform
  modules and refuses every existing destination. Local plans, state, caches, and tfvars never ship.
- Release publication accepts only an exact version tag and uses a reviewed `pypi` environment with
  trusted publishing. Wheel and sdist must both install and run outside the source checkout.

## 2026-08-01 — Exclusive runs, transactional fencing, and cursor CAS

- Named pipelines acquire one expiring lease and heartbeat it throughout execution. An overlap is
  recorded as a terminal skipped run; a heartbeat failure prevents subsequent finalization.
- Hosted DML finalizers must update the exact pipeline/run/fencing-token lease row inside their
  target transaction and assert that one row matched. A read-only ownership query is insufficient.
- Watermarks advance through compare-and-set from the pre-extraction boundary and share the hosted
  fence transaction. Sandbox replace stays atomic but does not claim transactionally fenced cloud
  publication in v0.1.

## 2026-08-01 — Focused bounded-memory ingestion

- The normal hosted SCD1 path consumes `platform.runtime.batch_rows` batches and performs an
  idempotent merge for each batch; the endpoint watermark advances only after all batches succeed.
- Sandbox replace streams the endpoint into one expiring run-scoped staging table and publishes it
  atomically after extraction. Handled failures remove staging; hard-crash staging expires.
- SCD2, snapshot, incremental-writer, and Storage Write orchestration intentionally keep their
  existing logical-batch behavior for v0.1 rather than inheriting a new session abstraction.

## 2026-08-01 — Manifest-owned production runtime configuration

- `platform.runtime` is one typed, repository-owned contract shared by all hosted jobs: CPU,
  memory, task timeout, Cloud Run retries, and the existing BigQuery writer-request row limit.
- `dander init` reads region, BigQuery location, runtime, and safety from `dander.yaml`; CLI values
  replace authored values only when their flags are explicitly supplied.
- `platform.safety.require_guarded_free_tier` conditionally adds the hosted preflight flag and
  cannot be true for an enabled runtime when the infrastructure cost guard is disabled. This phase
  does not stream extraction batches or alter pipeline definitions.

## 2026-08-01 — Hosted HubSpot activation and failure alerting

- The dedicated HubSpot test account intentionally retains company read/write scopes. Its private
  app token is versioned only in Secret Manager and remains readable only by the HubSpot runtime.
- The account keeps one clearly synthetic company at `dander-integration-sandbox.invalid` so an
  empty first extraction can bootstrap the nested raw relation. General empty-source schema
  creation remains a separate engine improvement.
- A private operator email is supplied at deploy time through `--failure-alert-email`, never in the
  public manifest. Terraform owns one channel and one exact-job failed-execution policy per hosted
  pipeline; the HubSpot cadence is 10:00 ET, one hour after Greenhouse.

## 2026-08-01 — Clean-project bootstrap compatibility and retention

- Current `gcloud storage` creates hardened buckets with boolean public-access prevention, then
  applies versioning and labels through `buckets update`; the CLI contract is regression-tested.
- Stage zero enables Cloud Resource Manager before the platform cost guard reads project metadata.
  Budget Pub/Sub uses Google's singular `billing-budget-alert` publisher identity, and the
  Terraform budget resource receives the bare billing-account ID expected by provider v6.50.
- Proof helpers always pass their requested GCP project to `gcloud`. At clean-proof completion the
  project had both schedulers paused and an empty HubSpot secret container; later reviewed changes
  enabled the schedules and added the operator-owned secret version. The cost guard remains
  simulation-only, and inventory is evidence rather than deletion authorization.

## 2026-07-31 — Approval-gated clean-project proof

- The manual proof derives an ephemeral manifest from `dander.yaml`, forces every schedule paused,
  and keeps both additive pipelines present. Optional Dataplex IAM is scoped only to the selected
  proof pipeline; no proof flag may silently replace another pipeline.
- Secret containers and IAM are applied before an optional HubSpot value is added. The value flows
  from a protected environment secret directly to Secret Manager and never enters Terraform,
  generated configuration, evidence, or logs.
- Every proof records a sanitized retained-resource inventory even after failure. “Teardown”
  evidence is inventory-only; deletion is a separate explicit operation and is never automated by
  the proof workflow.

## 2026-07-31 — End-to-end executor and durable metadata spine

- `PipelineExecutor` owns the full named-pipeline lifecycle: ingestion, selected model builds,
  generic tests, metadata projection, and one truthful terminal run record. Connector-level
  `PipelineRunner` remains reusable but no longer decides hosted success before transforms run.
- Cloud runs persist lifecycle checkpoints and one atomic per-pipeline semantic snapshot in the
  `dander_meta` dataset; sandbox runs use the same contracts in SQLite. Snapshots contain no rows,
  cursors, credentials, or exception text and replace the prior definition only after compilation.
- Governed metrics are typed model-sidecar definitions with a closed aggregation set and declared
  field. Dander projects the human definition and deterministic calculation to the same spine used
  for source, model, column, lineage, and test metadata.

## 2026-07-31 — Batteries-included initialization boundary

- `dander init --apply` owns stage zero, runtime image publication, and platform apply. Defaults
  derive the state bucket, bootstrap identity, operator artifact directory, active gcloud user,
  runtime enablement, and simulation-first USD 5 guard; advanced split-stage commands remain.
- The remote-state bucket is the sole imperative exception because Terraform cannot create the
  backend holding its own first state. The CLI creates it hardened and versioned, immediately
  imports it into permanent stage-zero state, and leaves all later changes under Terraform.
- The bootstrap identity receives billing administrator access only when a billing account is in
  scope. Google exposes no narrower predefined role containing `billing.accounts.setIamPolicy`,
  which platform Terraform needs to grant isolated runtimes read-only budget visibility.

## 2026-07-31 — Additive project manifest and hosted pipelines

- `dander.yaml` is the repository-owned source of truth for named pipelines. A pipeline binds one
  connector to selected transform roots, schedule policy, secret references, and stable resource
  names; secret values remain outside the manifest and Terraform.
- Hosted pipelines share the immutable runtime image and warehouse datasets but receive distinct
  Cloud Run jobs, Scheduler jobs, runtime identities, and scheduler identities. Secret Manager IAM
  is computed per secret and pipeline rather than granting every runtime every connector secret.
- The original Greenhouse resources migrate to the `greenhouse_jobs` map key through Terraform
  state moves. Adding HubSpot must create new resources without replacing Greenhouse.

## 2026-07-31 — Fork-owned CI and evidence surface

- The admin-owned `harrisonoconnorhover/dander` fork is the execution surface for CI, protected
  environments, and retained workflow evidence; upstream `WagnerJ-Dev/dander` remains the
  contribution and review record through PR #1.
- Moving the branch preserves commit identities, including `8dfdd92`. Only repository-scoped
  objects—pull requests, check runs, environments, secrets, and workflow URLs—are re-anchored.
- GitHub OIDC Workload Identity Federation must be reconfigured for the fork's exact repository
  and ref before any live proof is dispatched. No cloud mutation is implied by this decision.

## 2026-07-31 — Stage-zero state retention

- `infra/bootstrap-admin` retains only migration input and recovery material in secured,
  operator-managed local storage; its active bootstrap state is held in the permanent GCS backend.
  Operators must keep local state and backups encrypted and access-controlled outside the repository.
- The created platform-state bucket is versioned, non-public, uniformly access-controlled, and
  non-destructive (`force_destroy = false`); prior object generations are retained for recovery and
  are not removed by routine migrations.

## 2026-07-31 — Permanent stage-zero GCS backend

- `infra/bootstrap-admin` uses the existing GCS bucket with the fixed
  `dander/bootstrap-admin/state` prefix as its permanent backend; the platform root continues to
  use `dander/state`.
- The backend is partial by design: bucket and prefix are supplied at initialization, while
  credentials come from the operator's authenticated Google context and never enter Terraform
  configuration.
- Local stage-zero state is migration input and recovery material only. Object Versioning and GCS
  locking must be verified before migration, and state, plans, backups, secrets, raw HubSpot
  responses, and `.terraform/` contents remain outside GitHub.

## 2026-07-31 — Stage-zero operator artifact boundary

- `AdministrativeBootstrap` requires an operator artifact directory that resolves outside the
  repository checkout. It stores the saved plan there and places Terraform's `TF_DATA_DIR` in its
  dedicated `terraform-data` child directory.
- The operator artifact and Terraform data directories are mode `0700`; completed plans are mode
  `0600`. Terraform continues to run from `infra/bootstrap-admin`, and apply accepts only the exact
  absolute saved-plan path. Every Terraform subprocess uses `umask 077`, and pre-existing
  `terraform-data` or plan symlinks are rejected before Terraform starts.

## 2026-07-30 — Reproducible bootstrap verification

- Terraform creates a distinct `dander-bootstrap` identity for approved infrastructure runs. Its
  broader provisioning roles are never attached to Cloud Run, Scheduler, or GitHub WIF; workloads
  use the narrow runtime and scheduler identities instead.
- Terraform state is always initialized through the GCS backend. `dander verify deployment` reads
  the initialized backend metadata, pulls state read-only to prove reachability, and checks actual
  Google Cloud resources rather than trusting Terraform output.
- Verification writes a sanitized JSON artifact. Failed checks remain explicit and make the command
  fail, so evidence cannot claim a successful deployment after a partial bootstrap.

## 2026-07-29 — Bootstrap credentials and deployment identity

- Terraform creates Secret Manager containers and IAM bindings, but never secret versions or
  values. Operators add values out of band so credentials cannot enter plans or remote state.
- GitHub deployment uses OIDC Workload Identity Federation constrained to one repository and exact
  ref. The federated principal can impersonate only a dedicated deployer; that deployer writes only
  Dander's Artifact Registry repository and can act as only Dander's runtime accounts.
- Hosted runtime plans require an immutable image digest and billing account. Planning remains the
  CLI default, and applying still uses the exact saved plan after interactive confirmation.

## 2026-07-29 — Simulation-first integrated cost guard

- The bootstrap can package the tested handler and provision its project budget, Pub/Sub topic,
  least-privilege identity, and Gen 2 function, but the entire module remains opt-in.
- Simulation is the default. Live billing detachment requires `--live-cost-guard`, a reviewed saved
  plan, and confirmation that explicitly names the destructive behavior.
- Budget notifications are delayed and not a spending cap. Function deployment uses billable GCP
  services, so its plan is never represented as guaranteeing a zero-dollar outcome.

## 2026-07-29 — BigQuery history and append semantics

- Incremental batches use cursor validation plus SCD1 key merge; extraction and watermark state
  own the lower bound, while the writer owns rerun idempotence.
- Snapshots never update/delete history. They use a configured date/timestamp partition and
  suppress exact rows both across reruns and within one incoming batch.
- SCD2 computes changed rows once, then closes and inserts versions in one transaction. System
  columns are reserved and nested values compare through canonical BigQuery JSON rendering.

## 2026-07-29 — Incremental transform boundary

- Incremental model metadata explicitly names its unique key and cursor; these are never inferred
  from generic tests or column naming conventions.
- Builds include rows at or above the existing maximum cursor, deduplicate each key by latest
  cursor, and `MERGE`. Re-reading the boundary handles tied timestamps without losing rows and is
  safe because the merge is idempotent; canonical JSON is the deterministic final tie-breaker.

## 2026-07-29 — Concrete enterprise ingestion proof

- Workday RaaS is the first hand-rolled `EnterpriseSource`: connector config selects it explicitly,
  while downstream runtime/writer/state code continues depending only on `Source`.
- Response envelopes, page progression, cursor params, bounded retry/backoff, and scalar casts are
  owned by this path. Transport and sleeping are injected so tests use no tenant or credential.
- Schema discovery returns declarations only. Cast failures expose field/type contract names but
  never rejected row values.

## 2026-07-29 — Visual graph execution boundary

- Linear source-to-transform-to-target mappings compile to explicit-column BigQuery SQL. Scalar
  expressions are parsed and allow-listed; custom transformations resolve only through a trusted
  built-in registry, never `eval`, imports, or inline code.
- Target configuration now dispatches every declared write mode to its concrete idempotent writer.
- Join execution remains fail-closed because the current edge schema makes its target both the
  right join input and output. A distinct join-output node is required before execution is safe.

## 2026-07-29 — Enterprise authentication profiles

- OAuth2 JWT assertions use a secret-backed RSA key only during token acquisition. Tokens cache to
  the provider expiry or a conservative 300-second default for providers such as Salesforce that
  omit `expires_in`.
- OAuth1 TBA signs every method, base URI, query, and OAuth parameter using RFC 5849 normalization
  and NetSuite's HMAC-SHA256 profile; all four credentials are resolved fresh per request.
- Connector files contain only secret references. Signing, token transport, nonce, and clocks are
  injectable so the complete behavior is proven offline.

## 2026-07-29 — Executable join output

- An executable join belongs to a transform node that explicitly names its two predecessor inputs;
  the transform is the distinct output relation. Its two incoming edges own output-field mappings.
- The edge-level join shape remains loadable for compatibility but non-executable because its
  target cannot safely represent both a right input and output.
- Join SQL uses declared equality keys, explicit projected columns, and the same safe expression
  compiler as linear mappings.

## 2026-07-29 — Operational run history

- Pipeline runs record start and terminal status plus endpoint/row-count aggregates in the control
  plane: BigQuery for guarded/cloud execution and the existing SQLite file for sandbox execution.
- History never stores rows, cursors, credentials, assertions, or exception text. A history-update
  failure during pipeline failure is logged without masking the original pipeline exception.

## 2026-07-29 — Bounded BigQuery load jobs

- Every writer accepts a validated `max_batch_rows` contract (10,000 by default, 100,000 maximum
  in target config). One logical batch is validated/deduplicated before requests are split.
- The first load request truncates its destination and later chunks append, preserving replacement
  and unique-staging semantics without unbounded request payloads.

## 2026-07-29 — Controlled schema evolution

- Target-node fields become the writer's declared schema. Strict mode remains the default;
  additive mode emits idempotent nullable additions for supported BigQuery scalar types only.
- Additive evolution never drops columns, changes types/modes, or infers nested structures.
  Invalid and duplicate declarations fail before a load request.

## 2026-07-29 — Storage Write API workload path

- Load jobs remain the default for latency-insensitive batch work. Keyed SCD1/incremental targets
  can explicitly select `storage_write`.
- Storage Write uses an offset-checked pending stream into a uniquely named staging table,
  finalizes and atomically commits it, then runs the existing idempotent merge. Direct final-table
  streaming was rejected because a new stream on rerun could duplicate rows.
- The Python protobuf encoder supports the scalar types it can represent without ambiguous custom
  annotations; unsupported types fail before any staging mutation.

## 2026-07-29 — Hosted public pipeline tail

- The scheduled public connector builds and catalogs only `stg_greenhouse__jobs`; selecting this
  root avoids coupling a credential-free run to private Harvest candidate data.
- Transform tests run before the semantic registry is written. A failed ingestion or transform
  prevents all later publication.
- Local registry compilation is the hosted default. Dataplex storage requires an explicit
  bootstrap flag that separately enables its API and runtime IAM.

## 2026-07-29 — Standard REST source rate control

- A dlt connector with a rate policy receives a private session that applies token-bucket pacing,
  safe-read-only retries, and fixed or exponential backoff; connectors without a policy retain
  dlt's default session.
- Marketo's official client-credential query placement is supported explicitly while API calls
  continue using bearer headers. Connector files still contain references and tenant placeholders,
  never credential values.

## 2026-07-30 — Sensitive-system scope is hypothetical

- Workday, Xactly, Salesforce, NetSuite, and similar systems are target connector categories, not
  evidence that Dander derives from, connects to, or contains data from an existing company.
- Do not infer employer ownership, regulated-company affiliation, customer records, or HR records
  from the architecture note. Apply normal provenance and privacy review only when actual
  employer-owned material, credentials, or non-public data enters scope.

## 2026-07-30 — Deterministic synthetic vendor proof

- A loopback-only invented API is the default integration proof for Dander-controlled REST
  behavior; it contains no tenant identifiers, credentials, or copied vendor records.
- Cursor and Link pagination, duplicates, updates, and retryable failures are deterministic so the
  real dlt HTTP boundary can be validated repeatably without a vendor contract or cloud mutation.
- The packaged server proves extraction only. The normal CLI retains its explicit BigQuery write
  boundary rather than introducing a second local production storage mode for a demo.

## 2026-07-30 — Public data versus controlled test data

- Greenhouse remains the primary live demo; Lever and Ashby broaden real public response and
  pagination coverage without credentials or non-public records.
- Synthetic endpoints remain necessary for deliberate duplicates, updates, throttling, and server
  failures because public providers must not be manipulated to produce test failures.
- Candidate/contact-shaped tests use invented records in an owned test account. Public profiles are
  not treated as a substitute candidate dataset.

## 2026-08-01 — Declared raw schemas own hosted ingestion shape

- `Endpoint.raw_schema` is the complete recursive raw-table contract for project-defined hosted
  pipelines. Runtime normalization supplies missing nullable/repeated values and rejects
  undeclared, structurally invalid, or scalar-invalid fields without including values in errors.
- Empty targets are created from the declaration. Existing hosted SCD1 targets may gain only
  missing top-level `NULLABLE` fields; nested changes, type/mode changes, removals, and
  deployed-only fields fail before loading.
- Direct connector execution without a declaration remains temporarily compatible and emits a
  deprecation warning. Raw schema declarations do not project into the metadata spine in v0.1.

## 2026-08-02 — Salesforce starts as one standard REST vertical slice

- Salesforce Accounts use the existing dlt-backed source and OAuth2 JWT strategy. A distinct JWT
  audience and configurable assertion lifetime are required because Salesforce validates the
  authorization-server URL and a short-lived assertion independently from its token endpoint.
- QueryAll pagination follows Salesforce's opaque `nextRecordsUrl` as a JSON-carried URL. The
  provider `attributes` envelope is declared explicitly so strict raw-schema normalization cannot
  fail after otherwise successful extraction.
- The first slice fully rereads Accounts and publishes through idempotent SCD1 while recording a
  monotonic `SystemModstamp`. Parameterized incremental SOQL and Bulk API remain later scale work,
  not prerequisites for validating the authenticated product path.

## 2026-08-03 — PipelineGraph executes through connector bindings

- Connector YAML remains authoritative for API, authentication, pagination, raw schema, and
  cursor behavior. Executable source nodes name only `config.connector` and `config.endpoint`.
- The first hosted slice permits one connector with one or more selected endpoints and
  `replace` targets. Unsupported writer modes, inline requests, graph tests, mixed connectors,
  and graph metadata publication fail clearly instead of being ignored.
- Graph replacement stages compiled SQL output, then conditionally touches the active lease and
  replaces target rows with DML in one transaction. The existing `dander run` Cloud Run command
  remains the sole execution and deployment entrypoint.

## 2026-08-04 — NetSuite begins as a simulator-validated SuiteQL slice

- Customer extraction uses SuiteQL because NetSuite's record-collection endpoint returns IDs and
  HATEOAS links rather than selected field rows. The query uses a unique `ORDER BY id` and Dander
  removes SuiteQL's per-item transport links before raw-schema validation.
- The first slice fully rereads customers through bounded offset pages and relies on idempotent
  SCD1 publication while recording a monotonic watermark. SuiteQL's 100,000-result limit and
  offset paging under concurrent source mutation remain explicit scale boundaries.
- OAuth1 TBA is retained only to exercise Dander's existing compatibility strategy. Oracle's
  announced 2027.1 restriction on new TBA REST integrations makes current OAuth2 acceptance a
  release gate; until a real tenant passes, the connector is not NetSuite-validated or supported.

## 2026-08-04 — Odoo starts on JSON-2 against Community

- Odoo 19+'s JSON-2 API is the connector contract. New work does not depend on the deprecated
  XML-RPC/JSON-RPC endpoints.
- The first vertical slice reads only `res.partner` through the existing Source/runtime/writer
  boundary, using API-key bearer auth, bounded offset pages, and declared raw fields.
- Official Odoo Community and PostgreSQL containers are the free acceptance target because Odoo
  Online exposes its external API only on the Custom plan. Concurrently mutating large tables
  need later snapshot/keyset paging before this slice is described as scale-ready.

## 2026-08-04 — Salesforce scale moves to Bulk API 2.0

- Accounts use one asynchronous Bulk API 2.0 `queryAll` job. CSV result pages are streamed through
  the provider's opaque header locator and the query job is cleaned up after extraction.
- Hosted runs add an inclusive `SystemModstamp` predicate from Dander's committed watermark. The
  initial query and sandbox runs remain full reads, while SCD1 makes boundary replay idempotent.
- Dander deliberately omits `ORDER BY` and `LIMIT` because Salesforce documents that they disable
  Bulk API PK chunking. Polling remains bounded and a job without a completion SLA fails clearly.

## 2026-08-04 — Salesforce proves the connector plugin boundary

- Salesforce becomes the first independently installed connector while the behaviorally identical
  built-in adapter remains a deprecated 0.4 fallback. An exact manifest pin selects the plugin;
  undeclared installed packages remain inactive.
- Dander serves only presentation-safe installed-plugin descriptors to Druff. Connector YAML and
  Dander core remain authoritative for API settings, OAuth2 JWT authentication, secret references,
  execution, and publication.
- The live proof must use published Dander and plugin candidates. Functional discovery or plugin
  changes after `0.4.0rc1` therefore require `0.4.0rc2` before isolated acceptance.

## 2026-08-05 — Plugin authoring stays package-native

- A Dander scaffold creates one ordinary Python distribution with an API-v1 entry point, generic
  REST starting point, conformance test, and inert GitHub workflows. It does not create accounts,
  repositories, credentials, or marketplace records.
- Public conformance checks reuse the runtime registry's validator. Source construction is tested
  only when both a connector configuration and authentication strategy are supplied.
- PyPI remains the package store. Curated discovery and support claims stay separate from package
  execution, and the authoring slice targets the next Dander release after accepted `0.4.0`.

## 2026-08-05 — Connector discovery stays curated and package-backed

- Dander ships a small static catalog of reviewed connector packages rather than depending on a
  marketplace service or live PyPI queries. PyPI remains authoritative for package distribution.
- Catalog installation status comes only from the already validated manifest plugin registry;
  unrelated globally installed packages stay inactive and are not represented as project-installed.
- Druff may search and copy exact setup instructions, but it does not install packages, rewrite the
  manifest, or introduce a second plugin runtime.

## 2026-08-05 — Hosted Druff remains a public shell over a local control plane

- Dander may provision Druff's compiled source-free image as an optional scale-to-zero Cloud Run
  service, selected only by an explicit immutable image input rather than a new manifest mode.
- The hosted service receives a dedicated identity with no project roles and hosts no graph,
  secret, connector configuration, or execution API. Dander's exact-origin loopback service remains
  the only persistence and operational authority.
- Every full-platform plan, including Druff-triggered deployment previews, must repeat the current
  immutable Druff image to retain the service; omission visibly plans its removal.

## 2026-08-05 — Optional source capabilities remain structural and read-only

- Independently installed and built-in sources may structurally implement targeted lookup, cheap
  count, and connection-test protocols. A typed facade detects and invokes them without changing
  the mandatory `Source` contract or connector plugin API v1.
- Deleted-record feeds and provider create/update/delete operations remain absent until their
  cursor, retry, authorization, and destination semantics are approved separately. Josh Wagner's
  originating adapter work is preserved at `WagnerJ-Dev/dander@574d2f0`.

## 2026-08-05 — Pipeline operations execute after raw ingestion

- Josh Wagner's ordered operation vocabulary from `WagnerJ-Dev/dander@574d2f0` becomes typed
  `TransformNodeConfig.operations` on the existing canonical `PipelineGraph`.
- Trim, truncate, default, and bounded filters compile to explicit schema-preserving BigQuery CTEs
  inside the existing graph transform stage. Raw landing, cursor commits, leases, and connector
  plugins remain unchanged.
- The operator-bound graph service publishes only presentation-safe metadata for this executable
  subset; Druff edits the same `OperationSpec` objects stored in `PipelineGraph`.
- Rename/drop remain edge mappings. Deduplication, arbitrary SQL hooks, deleted feeds, and provider
  write-back require separate product decisions rather than entering through this slice.

## 2026-08-05 — `teammate/main` (Harrison's fork) becomes the trunk; local main resets onto it

- `WagnerJ-Dev/dander@main` was previously synced from the fork by squash-copying a snapshot
  (`23ef62a`), not merging, so git recorded no shared history past that point. The two lines then
  diverged for real: the fork advanced through 0.3.0–0.5.1 (this file's entries above), while local
  `main` gained one further squashed commit (`574d2f0`) independently building the same two seams
  — a connector capability adapter and a pipeline-operation framework — under different names and
  with broader scope (write-back, `get_deleted`, SQL hooks, deduplicate).
- Rather than attempt a mechanical merge across unrelated history, local `main` is reset directly
  onto `teammate/main` (`e87b03b`) and adopts it as the trunk going forward. The prior local line is
  preserved at `backup/local-main-pre-reconcile` (`574d2f0`), not deleted.
- The fork had already reconciled the safe, read-only/schema-preserving subset of `574d2f0` on its
  own initiative (see the two entries directly above) — confirmed by cross-reading
  `src/dander/ingestion/capabilities.py` and `src/dander/pipeline/operations.py` against
  `tickets/DANDER-64..76`. Tickets `64/65/67/68/69/71` are satisfied by the fork's
  `SourceCapabilities`/`operations.py` implementations (reconciliation notes added to each ticket)
  and need no further code. Tickets `66` (`get_deleted`), `70` (SQL hooks), `72` (narrowed to
  `deduplicate`), and `73..76` (write-back `create`/`update`/`upsert`/`delete`) remain genuinely
  open — the fork explicitly deferred them pending separate cursor/retry/authorization/destination
  design, which is the actual next step, not a mechanical port.
- `origin/main` (this repo's own remote) was force-pushed to match (`60dc73b`) after explicit
  confirmation, since it rewrites shared history rather than fast-forwarding it.

## 2026-08-05 — Write-back and deleted-record-feed semantics

Resolves the "separate product decisions" the two entries above deferred, unblocking
`tickets/DANDER-66` (`get_deleted`) and `DANDER-73..76` (`create`/`update`/`upsert`/`delete`).

- **Cursor:** `get_deleted(endpoint, *, since=None)` mirrors `Source.extract(endpoint,
  since=...)` exactly — same per-endpoint keying, same cursor type and meaning — so a downstream
  consumer can reconcile the insert/update stream and the delete stream off one watermark. No new
  cursor concept is introduced.
- **Retry:** `create` is non-idempotent — a caller MUST NOT blindly retry it after an ambiguous
  failure (e.g. a timeout where the write may or may not have landed) without an out-of-band
  reconciliation step. `update`, `upsert`, and `delete` are naturally idempotent (re-applying
  converges to the same source-system state; a repeat `delete` of an absent record returns
  `DeleteOutcome.NOT_FOUND` rather than raising), so ordinary bounded retry/backoff applies to
  them the same as any other source call.
- **Authorization:** no new credential path. Every write-back and `get_deleted` implementation
  resolves credentials through the source's already-wired `AuthStrategy`
  (`dander.security.base`), so access stays routed through the existing audited strategy per
  `steering/01-security.md`. These capabilities add no separate write-scoped credential or token.
- **Destination:** write-back operations write to the *source system*, not BigQuery — there is no
  BigQuery destination to decide for `create`/`update`/`upsert`/`delete` themselves. Consuming
  `get_deleted` to propagate hard deletes into a BigQuery target remains explicitly out of scope
  here, deferred to future write-pattern work building on `dander.writer`; this decision only
  makes the feed a typed, detectable capability.
- **Shape:** `SourceCapabilities` in `src/dander/ingestion/capabilities.py` gains
  `get_deleted`/`create`/`update`/`upsert`/`delete` accessor methods matching the existing
  `get_single_object`/`count`/`test_connection` pattern (`require()` guard, `cast` to the
  matching `Protocol`, result-shape validation raising `InvalidConnectorCapabilityResultError`
  where the result isn't a lazily-consumed iterator). No concrete source implements any of the
  five yet — this ships the mechanism and contract only, per the existing `ConnectorOperation`
  registry's Open/Closed extension pattern.

## 2026-08-05 — Hosted installation is plan-first by default

- The compatibility `dander init` path remains, while public installation and upgrades separate
  stage-zero planning, saved-plan application, immutable image publication, and platform planning.
- A read-only permission preflight tests the active identity before stage-zero Terraform. Optional
  billing and Workload Identity permissions are checked only when those features are selected.
- A cloud administrator may perform stage zero once; later image and platform operations use the
  bootstrap account through operator-scoped impersonation rather than Project Owner.

## 2026-08-06 — Salesforce becomes the first deep connector

- Accounts, Contacts, Opportunities, and Users retain separate inclusive `SystemModstamp` cursors
  behind the existing `salesforce_bulk2` engine and plugin API v1.
- QueryAll retains visible CRM tombstones; Users retain inactive owners through `IsActive`.
  Hard-deleted or purged records and provider write-back remain outside the contract.
- The governed fact excludes deleted Opportunities and Accounts while retaining inactive owner
  dimensions. Existing installations may keep the `salesforce_accounts` pipeline resource ID;
  new examples use `salesforce_crm`.

## 2026-08-07 — Cross-cloud feasibility uses one copied multi-platform artifact

- Runtime image publication now requires both `linux/amd64` and `linux/arm64` manifests in one OCI
  index. Phase 1B copies that exact index GAR-to-ECR with `crane`; any index or per-platform digest
  rewrite fails the gate rather than being treated as equivalent packaging.
- The proof Fargate task has a pull/log execution role and a distinct policy-free task role. Google
  trusts only the exact assumed task-role ARN through Workload Identity Federation and permits it
  to impersonate a disposable service account with BigQuery job access and read access to one
  proof dataset.
- A proof-only, source-free probe observes a 600-second impersonated Google credential expire and
  refresh in one process. Because Google Auth's AWS supplier does not consume Fargate's ECS
  credential endpoint directly, the probe validates that fixed link-local endpoint and exposes
  its short-lived task-role values only in the current process. The generated external-account
  config uses the library-supported `service_account_impersonation` lifetime field. This does not
  make Fargate a supported launcher; that remains Phase 3.

## 2026-08-08 — Destination publication has its own fence ledger

- A state lease identifies its immutable authority and epoch, but never assumes that its control
  table is transactionally reachable from a different destination warehouse.
- Each destination claims `(pipeline, target)` in `dander_target_commits` before staging. Only a
  newer token or an exact retry from the current authority/epoch may replace that claim.
- Final publication touches the exact authority/epoch/pipeline/target/run/token tuple and records
  completion in the same destination transaction. Cross-backend execution remains fail-closed
  until every writer and materialization caller uses this boundary.

## 2026-08-08 — PostgreSQL warehouse starts with bounded SCD1 publication

- PostgreSQL's first warehouse slice supports only the existing SCD1 contract. Dander supplies
  bounded batches; the adapter streams each batch through `COPY` into an `ON COMMIT DROP` temporary
  relation and selects the final ordinal per business key before upsert.
- The destination target fence is touched and committed in the same transaction as each batch.
  An exact run/token claim may therefore finalize multiple batches, while an older token remains
  unable to publish after a newer claim.
- PostgreSQL types derive from canonical schema v1. Only declared nullable additions evolve
  automatically; extra columns, type or nullability drift, required additions, and malformed
  records fail before target DML. Profile selection and transforms remain separate milestones.

## 2026-08-08 — PostgreSQL materializations use stable relations and transactional fencing

- Portable and explicitly PostgreSQL-authored models render through the existing transform
  project. A PostgreSQL project uses database-local `schema.relation` names without changing the
  default BigQuery compilation path.
- Table materialization keeps a stable relation and transactionally performs create-if-absent,
  truncate, and insert behind the exact target fence. Views use transactional
  `CREATE OR REPLACE`; incremental models use a unique index and deterministic
  `INSERT ... ON CONFLICT`.
- Generic not-null, unique, accepted-values, and relationship assertions share the model metadata
  but render PostgreSQL-native SQL. Graph execution and profile selection remain out of scope.

## 2026-08-08 — PostgreSQL is selectable as one native runtime composition

- Version 2 warehouse configuration is a discriminated BigQuery/PostgreSQL contract. `dander run`
  and the OCI runtime select one named deployment without requiring a GCP project for a fully
  PostgreSQL, no-catalog, environment-secret composition.
- A writer explicitly declares whether destination publication fencing is required. The neutral
  runner claims those targets before extraction; legacy BigQuery writers retain their existing
  state-side fence path unchanged.
- PostgreSQL state/warehouse and BigQuery-state/PostgreSQL-warehouse pairs are executable.
  PostgreSQL-state/BigQuery-warehouse remains fail-closed until all BigQuery write modes adopt the
  destination-side fence. Hosted support still requires the Kubernetes and live-profile gates.
## 2026-08-08 — Kubernetes targets existing clusters through a versioned Helm chart

- **Boundary:** Dander renders a named deployment into one packaged chart; it does not create a
  cluster, database, registry, external Secret, observability stack, or cloud-specific identity.
- **Safety:** CronJobs forbid overlap, Jobs have bounded deadlines/retries and TTL cleanup, and the
  runtime uses a read-only non-root pod with explicit resources. Durable Dander leases remain the
  final concurrency defense.
- **Qualification:** Plan rendering and read-only cluster verification are implemented. Kubernetes
  plus native PostgreSQL remains unsupported until an existing-cluster end-to-end proof passes.

## 2026-08-08 — Backend compatibility is package-owned and qualification stays explicit

- **Matrix:** The installed package publishes every current BigQuery/PostgreSQL state/warehouse
  pair through `dander runtime compatibility`; absent and unsupported pairs fail before provider
  construction.
- **Evidence:** BigQuery-state/PostgreSQL-warehouse accepts the resolved BigQuery authority in the
  PostgreSQL destination fence and rejects the older token after a newer claim. PostgreSQL native
  remains experimental until its Kubernetes live profile passes.
- **Scale:** The repository benchmark exercises bounded batches, independent pipeline concurrency,
  stale-fence rejection, and staging cleanup. A local smoke cannot claim the controlled-memory SLO.

## 2026-08-08 — Snowflake and Redshift share bounded Parquet artifacts, not loaders

- **Boundary:** One run-scoped session maps canonical schema v1 to bounded, compressed Parquet
  parts. Provider upload, remote stage lifecycle, `COPY`, and publication remain separate adapters.
- **Integrity:** Every part is owner-only and content-addressed with SHA-256; the deterministic
  manifest exposes counts and fingerprints but no row values, credentials, or provider locations.
- **Cleanup:** Normal exit removes only the exact owned run directory. One oversized record may
  exceed the logical-byte target but stays a singleton; remote adapters must enforce service limits.

## 2026-08-08 — Neutral orchestration carries canonical relation coordinates

- **Boundary:** Provider-neutral run, graph, transform, metadata, and writer orchestration carries
  `RelationRef(catalog, namespace, name)`. Warehouse providers own translation from their native
  configuration into those coordinates and back into provider SDK or SQL vocabulary.
- **Compatibility:** Existing `--project`, `--dataset`, `project_id`, and serialized catalog fields
  remain supported, but are translated at CLI or provider boundaries instead of defining the
  neutral runtime contract. BigQuery resource identities and defaults remain unchanged.
- **Correctness:** A selected raw namespace is shared by ingestion, transforms, and metadata;
  canonical source coordinates are preserved rather than reconstructed from BigQuery defaults.
  State backends keep their own control catalog and namespace in mixed-provider compositions.

## 2026-08-08 — Snowflake begins as an explicitly experimental scalar SCD1 slice

- **Coordinates:** Snowflake configuration translates native `database` and `schema` values into
  canonical relations. The neutral runtime never treats them as a GCP project or dataset.
- **Publication:** Each bounded batch becomes checksummed Parquet parts, loads through a temporary
  stage and temporary table, and merges behind the exact destination fencing token. Parquet logical
  types and binary values use explicit `COPY` settings; oversized singleton parts fail before
  remote staging.
- **Boundary:** Only scalar SCD1 ingestion is admitted. Transforms, graphs, semi-structured types,
  other write modes, and a support claim remain blocked on their own implementation and live proof.

## 2026-08-09 — Snowflake transforms admit only transactionally fenced materializations

- **Preflight:** The complete selected model DAG, schemas, SQL, materializations, and assertions
  compile before any Snowflake session or destination claim is opened.
- **Publication:** Table replacement and deterministic cursor-monotonic incremental merge use
  session-temporary staging and DML in the same transaction as the exact destination-fence touch.
- **Boundary:** Permanent view DDL, graph execution, and automatic transform-schema evolution fail
  closed. They cannot enter this slice without preserving the same publication guarantee.

## 2026-08-10 — Snowflake writer modes share one fenced staging path

- **Selection:** The provider-neutral warehouse writer capability accepts a logical mode plus the
  mode-specific cursor or snapshot field. Existing hosted ingestion translates explicitly to SCD1,
  so current CLI behavior is unchanged.
- **Publication:** Snowflake reuses one bounded Parquet/temporary-stage load for replace, SCD1,
  SCD2, snapshot, and incremental writes. Mode-specific DML and load-history rows commit with the
  exact destination-fence touch; replace is streamed as one logical publication.
- **Boundary:** This makes the modes reachable and testable but does not promote Snowflake support.
  Views, graph wiring, explicit VARIANT, direct-write crossover, telemetry, and live qualification
  remain separate gates.

## 2026-08-10 — Canonical schemas and operation telemetry survive orchestration

- **Schema:** Connector raw fields, graph fields, and model columns may carry validated provider
  extensions. The selected canonical `RelationSchema` is retained on `WriteTarget`; legacy
  BigQuery declarations remain available unchanged for API-v1 writers.
- **Telemetry:** Writers may drain completed operation telemetry without changing their `write()`
  return contract. Endpoint and transform results preserve those operations in execution order,
  and the executor includes them in terminal `RunTelemetry`.
- **Boundary:** This is additive plumbing only. Providers still own extension meaning and direct
  versus bulk selection; no warehouse gains support or new SQL behavior from this contract.

## 2026-08-09 — Redshift begins with IAM-only bounded SCD1 publication

- **Boundary:** Provisioned and Serverless profiles map native database/schema coordinates into
  canonical relations. The first slice supports scalar-schema SCD1 only; transforms, graphs,
  other write modes, SUPER fallback, infrastructure, and live qualification remain unavailable.
- **Loading:** Bounded Parquet parts use a mandatory, content-length S3 manifest in the warehouse
  region. Ambient AWS credentials upload and clean owned objects; SQL receives only a validated
  cluster/workgroup COPY-role ARN and never an AWS access key.
- **Correctness:** Claims serialize on the destination ledger. One publication transaction locks
  and DML-touches the exact authority/run/token, evolves declared nullable columns, performs the
  deterministic ordinal MERGE, records replay identity, and commits the fence or rolls back all.

## 2026-08-09 — Redshift transforms reuse native transactions and canonical relations

- **Preflight:** The complete selected model DAG, declared scalar schemas, SQL, materializations,
  and assertions compile before Redshift receives a connection, destination claim, or mutation.
- **Publication:** Session-temporary CTAS staging feeds table replacement or deterministic,
  cursor-monotonic incremental `UPDATE`/`INSERT`; target DML and the exact fence touch commit
  together.
- **Boundary:** Canonical database/schema/relation coordinates survive compilation, while the
  provider renders database-local target DML. Views, graphs, automatic transform-schema evolution,
  `SUPER`, and support promotion remain separate gates.

## 2026-08-09 — Glue projects the canonical catalog directly without crawlers

- **Coordinates:** One deterministic lowercase Glue database represents canonical
  `catalog + namespace`; one Glue table represents the relation. Lossy name normalization receives
  a stable digest instead of collapsing distinct warehouse objects.
- **Ownership:** Dander updates descriptions, columns, `classification`, and `dander.*` parameters
  while preserving unrelated database/table/storage/column metadata. It never deletes catalog
  objects or makes crawlers authoritative.
- **Boundary:** Ambient AWS identity and direct API readback are implemented. IAM infrastructure,
  Glue connections, Lake Formation, tags, live proof, and support promotion remain separate work.

## 2026-08-09 — Warehouse capabilities are exact and schema validation is fail-early

- **Contract:** Every warehouse runtime declares its implemented write modes, transports, model,
  graph, fencing, logical-type, decimal, and temporal support. The credential-free compatibility
  report mirrors those declarations and tests prevent drift.
- **Failure boundary:** Provider schema mappers reject unsupported types, precision, and nested
  arrays before source extraction, staging, or destination mutation. BigQuery retains its native
  v1 schema path because types such as GEOGRAPHY have no lossless canonical mapping. Diagnostics
  name the provider and
  exact field path without including provider responses or record values.
- **Scope:** The declaration reports implemented behavior only. It does not promote experimental
  providers, add write modes, introduce semi-structured fallbacks, or widen the supported profile
  manifest.

## 2026-08-09 — Fargate-to-Google identity renews at the client boundary

- **Identity:** Google Auth receives a process-local AWS credential supplier that refetches the
  current temporary task-role session from Fargate's fixed link-local endpoint for every subject
  token refresh. Dander no longer copies task secrets into global environment variables or writes
  an external-account file for normal runtime execution.
- **Boundary:** The credential is scoped to one OCI runtime invocation and passed explicitly only
  when existing Google clients are constructed. Cloud Run and local execution continue to use
  normal Application Default Credentials without changed resource or IAM behavior.
- **Support:** Renewable identity removes the one-hour credential limit and permits an enforced
  24-hour launcher deadline, but does not promote Fargate before the complete live lifecycle gate.

## 2026-08-10 — Snowflake query telemetry is same-session and best-effort

- Dander enriches only completed Snowflake operation IDs through one bounded
  `INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION` lookup on their existing connection.
- The lookup selects stable counters only, caps requests at the most recent 1,000 IDs, and never
  selects query text, bind values, provider errors, or response payloads.
- Missing history cannot fail a successful pipeline. Delayed account-usage cost attribution and
  misleading output-row-as-read-row mappings are explicitly excluded.

## 2026-08-10 — Redshift writer modes share one fenced COPY publication path

- **Selection:** The provider writer factory accepts replace, SCD1, SCD2, snapshot, and incremental
  modes plus their existing mode-specific fields. Existing hosted ingestion still selects SCD1.
- **Publication:** Bounded Parquet/S3 staging feeds mode-specific DML. Target mutation, exact replay
  identity, and the destination-fence touch commit in one transaction; replace and SCD2 consume one
  complete logical stream so batch boundaries cannot change their semantics.
- **Boundary:** This is local conformance evidence, not a support promotion. Direct transport,
  `SUPER`, graphs, views, infrastructure, and live Redshift qualification remain separate gates.

## 2026-08-10 — Redshift SUPER is an explicit strict-JSON fallback

- **Selection:** Only canonical JSON carrying `redshift/fallback=super` maps to `SUPER`; bare JSON,
  ARRAY/RECORD, unrelated Redshift extensions, and SUPER keys/cursors/snapshot fields fail closed.
- **Loading:** Dander validates strict JSON locally, serializes deterministic UTF-8 bytes into an
  explicitly sized VARBYTE Parquet column, and applies `JSON_PARSE` within fenced publication DML.
  This avoids the 65,535-byte stored-VARCHAR boundary.
- **Boundary:** The current 4 MB staged-row guard remains stricter than Redshift's 16 MB SUPER
  service limit. This is local conformance behavior, not live qualification or support promotion.

## 2026-08-10 — Redshift graphs reuse the canonical AST and fenced table path

- **Planning:** Redshift consumes the existing `GraphExecutionPlan` and renders each compiled
  relational AST with the Redshift dialect; it does not introduce a provider-specific graph model.
- **Publication:** Every selected replace target preflights before provider I/O, then uses the same
  run-scoped CTAS staging, exact destination-fence transaction, stable target, and cleanup path as
  Redshift table models.
- **Boundary:** Graphs remain single-connector and replace-only. Redshift safe casts fail preflight
  until an exact lowering exists. Views, telemetry expansion, live AWS proof, and support promotion
  remain separate Phase 5 slices.
