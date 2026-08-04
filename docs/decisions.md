# Engineering Decisions

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
