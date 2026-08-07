# Hybrid ingestion

Dander has two extraction families behind the same `Source` contract:

- `DltRestSource` translates ordinary declarative REST connectors into dlt resources.
- `WorkdayRaasSource` owns the request loop for Workday RaaS reports: OAuth/basic auth strategy
  application, response-envelope selection, page-number pagination, cursor params, bounded
  rate-limit backoff, and explicit BigQuery scalar casts.
- `NetSuiteSuiteQLSource` is the hand-rolled SuiteQL POST path: OAuth1 TBA application, required
  headers, validated offset-page metadata, and removal of NetSuite's per-item transport links.

Set `engine: workday_raas` in connector YAML to select the hand-rolled path. Its HTTP transport and
sleeper are injected seams, so the full behavior is testable without a tenant or credential.
`discover()` reports declarations only and never samples employee rows.

Set `engine: netsuite_suiteql` for the simulator-validated NetSuite customer slice. Its tracked
contract and real-tenant acceptance boundary are documented in
[`docs/netsuite-simulator.md`](../../../docs/netsuite-simulator.md).

Standard REST pagination includes offset, page number, JSON cursor, RFC 5988 Link header, and a
JSON-carried next-page URL. The last form exists for APIs such as Salesforce Query/QueryAll, whose
opaque `nextRecordsUrl` must be followed as a URL rather than copied into a query parameter.

Enterprise casts currently cover `BOOL`, `DATE`, `FLOAT64`, `INT64`, `NUMERIC`, `STRING`, and
timezone-aware `TIMESTAMP`. Cast errors name only the endpoint/field/type contract, never values.
Automatic nested-record schema evolution remains separate work.

The stateful local acceptance service, its six-operation contract, named failure scenarios, and
the boundary for a later tenant proof are documented in
[`docs/workday-simulator.md`](../../../docs/workday-simulator.md).

## Synthetic vendor proof

Run `uv run dander-synthetic-api` to start an entirely local, credential-free SaaS facsimile on
`127.0.0.1:8765`. The matching `connectors/synthetic_vendor.yaml` exercises JSON cursor and
Link-header pagination, duplicate business keys, incremental updates, and bounded recovery from a
deterministic 429 and 500. The integration tests call it over real HTTP; no vendor tenant or cloud
resource is involved.

Greenhouse is the primary live public demo. The `lever_job_board` connector adds real published
jobs with offset pagination, and `ashby_job_board` adds a second real ATS response envelope. Their
offline tests pin request construction; live data counts are deliberately not asserted because
public postings change. Static `query_params` may configure non-secret response options, while
credential-like parameter names fail validation and must be handled by `auth_strategy`.

## Optional capability discovery (`capabilities.py`)

`Source.extract()`/`discover()` remain the one mandatory contract. `SourceCapabilities` wraps a
concrete `Source` and structurally detects which optional operations it also implements —
targeted lookup, a deleted-record feed, cheap counts, a connectivity probe, and opt-in write-back
(`create`/`update`/`upsert`/`delete`) — via `runtime_checkable` Protocols, so callers check
`capabilities.supports(op)` before dispatch instead of hitting an `AttributeError`. `create` is
non-idempotent; `update`/`upsert`/`delete` are naturally idempotent. Every write-back
implementation reuses the source's existing audited `AuthStrategy` rather than a separate
credential path, and write-back always targets the source system, never BigQuery. See
`docs/decisions.md`, "2026-08-05 — Write-back and deleted-record-feed semantics."
