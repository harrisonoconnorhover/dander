# Changelog

Release notes for Dander are kept here and copied into the matching GitHub Release. Dander follows
semantic versioning while it is alpha: released minor lines receive fixes only, and new product
capabilities enter through the next minor release.

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
