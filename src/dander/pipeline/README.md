# `dander.pipeline`

## Role

`dander.pipeline` owns the **declarative pipeline graph** — the durable primitive behind both a
future drag-drop UI and fully code-authored pipelines. A graph is a list of `nodes` (data objects /
tasks) and `edges` (connections between them, i.e. how data flows). The package defines the
on-disk shape (YAML/JSON), validates that a graph is structurally and semantically sound, derives
read-only structure (adjacency, topological order), safely compiles the supported graph subset to
BigQuery SQL, and prepares configured target writers. Ingestion, query submission, and scheduled
execution remain in the Ingestion/Transform/Writer/Orchestration layers described in
`steering/00-project-overview.md`.

This doc is accurate to the pipeline package as merged. If the code and this doc ever disagree, the
code is right and this doc has drifted — please fix it.

## Package layout

| Module | Responsibility |
|---|---|
| `dander.pipeline.graph` | The model: `Node`, `NodeField`, `Edge`, `FieldMapping`, `Transformation`, `JoinSpec`, `CursorStrategy`, `PipelineGraph`, and YAML/JSON load/dump. Pure value objects — no validation logic beyond Pydantic's own boundary constraints. |
| `dander.pipeline.graph_ops` | The correctness layer: structural `validate`, `topological_order`, `AdjacencyIndex`, and field-wiring `validate_field_wiring`. Pure functions of a `PipelineGraph` — nothing is persisted onto the model. |
| `dander.pipeline.graph_service` | Loopback-only, single-file bridge for visual editors. Dander validates conditional saves and can bind that exact graph to one already-deployed Cloud Run job selected at startup. |
| `dander.pipeline.node_config` | The discriminated, per-node-type `Node.config` models — `NodeType`, `NodeConfig`, `SourceNodeConfig`, `TransformNodeConfig`, `TargetNodeConfig` — and the routing function `Node` delegates to. See *Typed per-node-type config* below. |
| `dander.pipeline.compiler` | Safe compilation of executable linear/two-input graphs to explicit BigQuery SQL, plus side-effect-free target-writer preparation. |
| `dander.pipeline.request_spec` | `SourceNodeConfig`'s declarative request/payload spec — `HttpMethod`, `RequestSpec` — and the secret/field-reference grammar its header/param/body values must follow. See *Source request/payload spec* below. |
| `dander.pipeline.errors` | The typed `GraphValidationError` hierarchy raised by `graph_ops`. |

### Import paths (what's actually exported where)

The package `__init__.py` (`dander.pipeline`) re-exports the graph shape (`Node`, `Edge`,
`PipelineGraph`, the four `load_*`/`dump_*` functions), the `graph_ops` functions
(`validate`, `validate_field_wiring`, `topological_order`, `AdjacencyIndex`), the full `errors`
hierarchy, and the typed node-config classes (`NodeType`, `NodeConfig`, `SourceNodeConfig`,
`TransformNodeConfig`, `TargetNodeConfig`, `HttpMethod`, `RequestSpec`), the executable
join/writer configuration classes, and compiler entry points (`compile_target`,
`prepare_target_writer`). **It does not currently re-export the finer-grained graph model
classes** — `NodeField`, `FieldMapping`, `Transformation`,
`TransformationKind`, `JoinSpec`, `JoinKeyPair`, `JoinType`, `CursorStrategy`, `CursorKind` —
those must be imported from the submodule directly:

```python
from dander.pipeline import Node, Edge, PipelineGraph, validate, validate_field_wiring
from dander.pipeline import SourceNodeConfig, HttpMethod, RequestSpec
from dander.pipeline.graph import NodeField, FieldMapping, Transformation, TransformationKind
from dander.pipeline.graph import JoinSpec, JoinKeyPair, JoinType
from dander.pipeline.graph import CursorStrategy, CursorKind
from dander.pipeline.errors import DuplicateFieldNameError, UnknownFieldReferenceError
```

(`CursorKind`/`CursorStrategy` follow the same asymmetry as the rest of this list — see the note
below.)

(See *Implementation Notes* in `tickets/DANDER-9-pipeline-fields-mappings-docs.md` for a note on
this asymmetry.)

## Node field schema

A `Node` may declare an ordered `fields: list[NodeField]` — the schema it exposes (e.g. the columns
a `source` node produces). It defaults to empty, so a fieldless node is unchanged from the original
`Node`/`Edge` shape. Each `NodeField` has:

| Key | Meaning |
|---|---|
| `name` | Required identifier for the field. |
| `type` | Free-form type token (e.g. a BigQuery-ish `STRING`/`INT64`). Not yet validated against a closed set — same treatment as `Node.type`. |
| `nullable` | Whether the field may be null. Defaults to `true`. |
| `description` | Optional human-readable documentation. |
| `metadata` | Free-form dict, default `{}`. **Tags/labels only** (e.g. a `sensitivity`/`pii` classification) — never a real field value or sample data (`steering/01-security.md`). |

## Connection field-to-field mapping

An `Edge` may declare an ordered `mappings: list[FieldMapping]` — column-level lineage across the
connection. Each `FieldMapping` is:

| Key | Meaning |
|---|---|
| `source` | The source-node field **name** this mapping reads from. `None` for a derived/computed target with no single source column — in that case a `transformation` of kind `expression` or `constant` is required. |
| `target` | The target-node field **name** this mapping writes to. Always required. |
| `transformation` | Optional; see below. `None` means a plain direct copy. |
| `metadata` | Free-form tags/labels only, default `{}`. |

Both `source` and `target` reference fields **by name**, never by value. Whether those names
actually resolve on the edge's connected nodes is a *validation-layer* concern (below), not
something `FieldMapping` itself checks.

## Connection transformations

A `FieldMapping` may carry a `transformation: Transformation | None`. A `Transformation` is
**opaque and inert** — an `expression` string is never parsed or evaluated here, and a `constant`
literal is never interpreted; both are stored as-authored for the Transform/Writer layer to execute
later. Neither may embed a secret or credential — a transformation references fields and functions,
never values that belong in Secret Manager / env (`steering/01-security.md`).

`kind: TransformationKind` is a closed `StrEnum` with three values:

| Kind | Payload |
|---|---|
| `direct` (default) | No `expression`, no `constant` — a plain field copy. |
| `expression` | Requires a non-empty `expression` string (e.g. `"UPPER(full_name)"`); `constant` must be unset. |
| `constant` | Requires an explicit `constant` literal to be **set** (including `null` — presence, not truthiness, is checked, so a legitimate constant `null` is distinguishable from "not provided"); `expression` must be unset. |

`inputs: list[str]` names zero or more source-field names the transformation references (checked
downstream by field-wiring validation, not by the model itself). `metadata` is free-form tags only.

## Join specification

An `Edge` may carry an optional `join: JoinSpec | None` for a connection that **combines two
sources**. `None` (the default) is a plain edge, unchanged and backward-compatible with graphs that
predate joins. `JoinSpec` is opaque and inert: **no SQL is generated and no join is executed here**
— it records join *intent* for the Transform layer.

- `type: JoinType` — a closed `StrEnum`: `inner`, `left`, `right`, `full`. An out-of-set value fails
  Pydantic validation.
- `keys: list[JoinKeyPair]` — ordered equality key pairs; **at least one is required** (an empty
  list raises a `ValidationError`). Declaration order is preserved.
- `metadata` — free-form tags only.

**Left/right orientation:** the join's left side is always the edge's `from` node (`Edge.source`)
and the right side is always the edge's `to` node (`Edge.target`). Each `JoinKeyPair` pairs a
`left` field name (on the `from` node) with a `right` field name (on the `to` node). *Left*/*right*
is used for field names deliberately, to avoid colliding with `source`/`target`, which already name
node **ids** on `Edge`.

*(Product note: representing join semantics on the graph — vs. leaving joins entirely to the
Transform layer — was a genuine fork; the graph took the in-scope, declarative interpretation. See
Implementation Notes below for the Decision Log status of this call.)*

## Executable linear pipelines

`compile_target` turns a validated graph into an explicit-column BigQuery `SELECT`. It supports
linear mappings plus two-input joins whose output is a distinct transform node, JSON constants,
scalar expressions, target casts, and the built-in custom-transform registry. Expressions are
parsed with sqlglot, must reference exactly their declared `inputs`, may use only allow-listed
scalar functions, and cannot contain queries, tables, stars, or parameters. No expression is
passed to Python `eval`.

`prepare_target_writer` resolves a target's `WriterConfig` to the concrete SCD1, SCD2, snapshot,
incremental, or replace writer and a `WriteTarget`. Constructing it has no network side effect;
calling its `write()` method performs the configured BigQuery write.

An executable join is authored on `TransformNodeConfig.join`: `left_input` and `right_input` must
match its two incoming edges, `keys` name equality columns on those inputs, and the transform node
is the output. The older `Edge.join` shape remains readable for backward compatibility but fails
closed during compilation because it makes the edge target both a right input and the output.

## Node cursor / watermark strategy

A `Node` may carry an optional `cursor: CursorStrategy | None` (DANDER-18) declaring how a
source/ingestion node resumes incrementally. `None` (the default) is a plain node, unchanged and
backward-compatible with graphs that predate cursor strategies. `CursorStrategy` is opaque and
inert: **no state is read, written, or persisted by the graph model** — it records cursor intent.
Endpoint execution can honor the equivalent connector cursor through
`dander.state.watermark.WatermarkStore`; graph-to-runtime cursor binding beyond that connector
path is implemented when a source node declares `config.connector` and `config.endpoint` that
match the hosted pipeline's connector YAML.

- `field: str` — the cursor field name the source advances on (e.g. `updated_at`). Referenced by
  name only, never a value. Required and non-empty after stripping (a whitespace-only `field`
  raises a `ValidationError`, same treatment as `Transformation.expression`).
- `kind: CursorKind` — a closed `StrEnum`: `timestamp`, `sequence`, `opaque_token`. An out-of-set
  value fails Pydantic validation. Required — there is no sensible default kind.
- `params` — free-form, kind-specific parameters (e.g. a timestamp format hint). Opaque and inert,
  never interpreted here; never a secret (`steering/01-security.md`).
- `metadata` — free-form tags/labels only, default `{}`.

**Migration from `Endpoint.incremental_cursor`.** Before DANDER-18, `dander.ingestion.source.
Endpoint.incremental_cursor` was the only place a cursor was named — just a field name string,
with no kind and disconnected from the pipeline graph. That field is kept unchanged so existing
connector YAML keeps loading, but it is now documented as the legacy, narrow form that
`Node.cursor` supersedes. `CursorStrategy.from_incremental_cursor(cursor_field: str | None) ->
CursorStrategy | None` is the clean mapping bridge: `None`/empty maps to `None`; a non-empty
string maps to `CursorStrategy(field=cursor_field, kind=CursorKind.TIMESTAMP)`. The `TIMESTAMP`
default is a **documented assumption** (the historical `Source.extract(..., since=...)` contract
treats the cursor as a monotonic "since" bound, which is timestamp-shaped by default) — a legacy
endpoint whose cursor was actually a sequence/token must author an explicit node-level
`CursorStrategy` rather than relying on this helper. The classmethod takes a plain `str | None`
(not an `Endpoint`), so `dander.pipeline` gains no import dependency on `dander.ingestion`.

A cursor-less node omits its `cursor` key entirely on dump (not `cursor: null`), matching the
join-less-`join` omission above.

```yaml
- id: extract_candidates
  type: source
  name: Extract Candidates
  cursor:
    field: updated_at
    kind: timestamp
```

*(Product note: surfacing the cursor strategy on the graph node — vs. leaving it entirely to the
Orchestration/State layer — mirrors the DANDER-7 join-on-the-graph call. See Implementation Notes
below for the Decision Log status of this call.)*

## Typed per-node-type config

`Node.config` is no longer a single opaque `dict` for every node regardless of `type`. It is
routed, by `Node.type`, to a distinct Pydantic model:

| `Node.type` | Config model |
|---|---|
| `source` | `SourceNodeConfig` |
| `transform` | `TransformNodeConfig` |
| `target` | `TargetNodeConfig` |
| anything else (e.g. `task`) | plain `dict` — the pre-DANDER-10 free-form behavior, unchanged |

All three subclass `NodeConfig` (`extra="allow"`, so config content with no dedicated field yet is
preserved losslessly rather than rejected) and are **distinct classes**, so a `source` node's
`config` can never silently be a `TargetNodeConfig` instance (and vice versa) — constructing a
`Node` with a mismatched typed config raises a `ValidationError` naming both class names and the
node's `type`, without echoing any config value. A modeled node with no `config` key loads as an
empty typed model (e.g. `TargetNodeConfig()`), so this is fully backward compatible with
DANDER-2..9 graphs. `SourceNodeConfig` currently carries one dedicated field beyond the inherited
`extra` bucket — `request` — described next. `TransformNodeConfig` carries executable join
configuration, and `TargetNodeConfig` carries its validated writer/destination contract.

## Source request/payload spec

A `source` node's `config` may carry an optional `request: RequestSpec | None` describing *how*
it calls its API — the HTTP method, headers, query params, and a request body template. `None`
(the default) is a plain, spec-less GET, unchanged from a pre-DANDER-11 source node; its `request`
key is omitted entirely on dump (not written as `request: null`), matching the join-less-`join`
omission above.

`RequestSpec` is **inert**: nothing in this package sends a request, resolves a secret, or renders
`body` — that happens in the Ingestion layer. Its fields:

| Key | Meaning |
|---|---|
| `method` | One of the closed `HttpMethod` set: `GET` (default) / `POST` / `PUT` / `PATCH` / `DELETE`. |
| `headers` | Request headers, name -> value. |
| `query_params` | Query-string parameters, name -> value. Named `query_params` (not `params`) to avoid colliding with the `config`/`params` alias on `Node`. |
| `body` | A JSON-object body template, a raw string template (e.g. GraphQL), or `None`. |

**Header/query-param/body values are secret references or field references only — never an inline
secret literal** (`steering/01-security.md`). Recognized references (never resolved here):

| Kind | Forms |
|---|---|
| Secret reference | `secret:<name>`, `env:<VAR_NAME>`, or a Secret Manager resource name (`projects/…/secrets/…/versions/…`) — consistent with `SourceConfig.auth_ref` (`dander.ingestion.source`). |
| Field reference | `field:<field_name>` or mustache `{{ <field_name> }}`. |

Two rules enforce this, applied by a `model_validator` on `RequestSpec`:

- **Rule A (deterministic).** A header/param whose *name* is in a documented sensitive set
  (`Authorization`, `Proxy-Authorization`, `X-Api-Key`, `Api-Key`, `X-Auth-Token`, `Cookie`,
  `Set-Cookie` for headers; `api_key`, `access_token`, `token`, `secret`, `client_secret`,
  `password`, and hyphenated variants for query params — all case-insensitive) **must** be a
  recognized reference; a literal there raises a `ValidationError`.
- **Rule B (defense in depth, best-effort).** Any value anywhere — including nested inside
  `body` — that is not a recognized reference and *looks like* a raw credential (an
  `Authorization`-style scheme prefix, a PEM block, a known key prefix like Stripe's `sk_`/AWS's
  `AKIA`/GitHub's `ghp_`/Slack's `xox*`, or a long whitespace-free base64/hex-ish run mixing
  letters and digits) is rejected too. This is a heuristic, not a proof — the guarantee for the
  positions that actually carry credentials is Rule A; Rule B is a tripwire for everywhere else.

Error messages name only the **position** (`header '<name>'`, `query param '<name>'`, or `body`)
and the rule violated — **never the offending value** (`steering/01-security.md`).

```yaml
config:
  endpoint: /candidates
  request:
    method: POST
    headers:
      Authorization: "secret:candidate_source_api_key"   # reference -- Rule A requires this
      Content-Type: "application/json"                    # benign literal -- both rules pass it
    query_params:
      since: "field:updated_since"                        # reference to a declared field
    body:
      query: "field:graphql_query"
      variables:
        candidate_id: "field:candidate_id"
```

`is_secret_reference`, `is_field_reference`, `is_reference`, and `looks_like_raw_credential` are
also exported from `dander.pipeline.request_spec` as standalone, unit-testable pure functions.

## Validation layer

Two tiers, run in a fixed order — structural first, field-wiring second — because field-wiring
checks assume the invariants structural validation guarantees (unique node ids, resolvable edge
endpoints).

### 1. Structural — `graph_ops.validate(graph)`

Four checks, in order:

| Check | Error |
|---|---|
| Node ids unique | `DuplicateNodeIdError` |
| Every edge endpoint references an existing node id | `DanglingEdgeError` |
| No edge is a self-loop | `SelfLoopError` |
| The graph is a DAG (no cycle) | `GraphCycleError` |

`graph_ops.topological_order(graph)` runs `validate` first, then returns node ids in a valid
execution order (every edge's source before its target).

### 2. Field-wiring — `graph_ops.validate_field_wiring(graph)`

Runs `validate` first (so a structural fault always surfaces before a field-wiring fault), then:

| Check | Error |
|---|---|
| No two fields on the same node share a `name` | `DuplicateFieldNameError` |
| Every `FieldMapping.source`/`.target` resolves on the edge's source/target node | `UnknownFieldReferenceError` |
| Every `Transformation.inputs` entry resolves on the edge's source node | `UnknownFieldReferenceError` |
| Every `JoinKeyPair.left`/`.right` resolves on the edge's source(left)/target(right) node | `JoinKeyFieldError` (subclass of `UnknownFieldReferenceError`) |

All of the above subclass `GraphValidationError`, so a caller that just wants "did this graph
validate" can catch that one root type; a caller that wants to distinguish failure modes catches
the specific subclass. **Error messages carry structure only** — node ids, edge endpoint ids, and
field names — and never a node's `config`, a field's/edge's `metadata`, a field value, or a
transformation's `expression`/`constant` payload (`steering/01-security.md`).

## Annotated end-to-end example (YAML)

Fake, non-sensitive names throughout. `crm_contacts` is a source node with two declared fields;
`warehouse_customers` is a target node with two declared fields; the connection between them has a
direct mapping, a derived field driven by an `expression` transformation, and a `left` join keyed
on `contact_id` ↔ `customer_id`.

```yaml
name: crm_to_warehouse_example
nodes:
  # A source node declaring the fields it exposes.
  - id: crm_contacts
    type: source
    name: Extract CRM Contacts
    config:                        # node-specific config; `params` also accepted on load
      endpoint: /contacts
    fields:
      - name: contact_id
        type: STRING
        nullable: false
        description: Unique contact identifier from the CRM.
      - name: full_name
        type: STRING
        description: Contact's full name as entered in the CRM.
        metadata:
          sensitivity: pii          # tag only -- never a real value

  # A target node declaring the fields it accepts.
  - id: warehouse_customers
    type: target
    name: Load Warehouse Customers
    fields:
      - name: customer_id
        type: STRING
        nullable: false
      - name: display_name
        type: STRING

edges:
  - from: crm_contacts               # on-disk key is `from` (Edge.source)
    to: warehouse_customers          # on-disk key is `to` (Edge.target)
    mappings:
      # A plain direct mapping: crm_contacts.contact_id -> warehouse_customers.customer_id.
      - source: contact_id
        target: customer_id
      # A derived field: no single source column, so `transformation` is required.
      - target: display_name
        transformation:
          kind: expression
          expression: "UPPER(full_name)"   # opaque string; evaluated downstream, not here
          inputs:
            - full_name                     # declares the field this expression reads
    join:
      type: left                            # left = crm_contacts (from), right = warehouse_customers (to)
      keys:
        - left: contact_id
          right: customer_id
```

This example is authorable-valid: it loads via `load_graph_from_yaml` and passes
`validate_field_wiring` unmodified (verified while writing this doc — not a committed test, per
the ticket's design).

## JSON form & on-disk keys

The same graph, dumped via `dump_graph_to_json` (equivalent to `dump_graph_to_yaml`, same keys):

```json
{
  "name": "crm_to_warehouse_example",
  "nodes": [
    {
      "id": "crm_contacts",
      "type": "source",
      "name": "Extract CRM Contacts",
      "config": { "endpoint": "/contacts" },
      "fields": [
        {
          "name": "contact_id",
          "type": "STRING",
          "nullable": false,
          "description": "Unique contact identifier from the CRM.",
          "metadata": {}
        },
        {
          "name": "full_name",
          "type": "STRING",
          "nullable": true,
          "description": "Contact's full name as entered in the CRM.",
          "metadata": { "sensitivity": "pii" }
        }
      ]
    },
    {
      "id": "warehouse_customers",
      "type": "target",
      "name": "Load Warehouse Customers",
      "config": {},
      "fields": [
        { "name": "customer_id", "type": "STRING", "nullable": false, "description": null, "metadata": {} },
        { "name": "display_name", "type": "STRING", "nullable": true, "description": null, "metadata": {} }
      ]
    }
  ],
  "edges": [
    {
      "from": "crm_contacts",
      "to": "warehouse_customers",
      "metadata": {},
      "mappings": [
        { "source": "contact_id", "target": "customer_id", "transformation": null, "metadata": {} },
        {
          "source": null,
          "target": "display_name",
          "transformation": {
            "kind": "expression",
            "expression": "UPPER(full_name)",
            "constant": null,
            "inputs": ["full_name"],
            "metadata": {}
          },
          "metadata": {}
        }
      ],
      "join": {
        "type": "left",
        "keys": [{ "left": "contact_id", "right": "customer_id" }],
        "metadata": {}
      }
    }
  ]
}
```

On-disk / alias keys to know, so both serializations are covered:

| Attribute | On-disk key(s) | Notes |
|---|---|---|
| `Edge.source` | `from` | Reserved word, so the Python attribute is `source`; dumps **always** emit `from` (`serialize_by_alias=True`), never `source`. |
| `Edge.target` | `to` | Same pattern as `from`; dumps always emit `to`, never `target`. |
| `Node.config` | `config` **or** `params` (load only) | Either key populates `Node.config` on load (`AliasChoices`); dumps canonically as `config` only. |
| `FieldMapping.source` / `.target` | `source` / `target` | Field-name strings; no aliasing beyond the plain keys. |
| `JoinKeyPair.left` / `.right` | `left` / `right` | Plain keys, no aliasing. |
| `Edge.join` | `join` | Omitted entirely (not `null`) when `Edge.join is None`, so join-less edges round-trip byte-identical to pre-join graphs. |
| `Node.cursor` | `cursor` | Omitted entirely (not `null`) when `Node.cursor is None`, so cursor-less nodes round-trip byte-identical to pre-DANDER-18 graphs. |

## Local visual-editor write-back

Expose exactly one existing graph file to Druff from the project/operator terminal:

```bash
dander graph serve --file /absolute/path/to/pipeline.yaml
```

The service binds to `127.0.0.1:8765` and accepts only the configured browser origin
(`http://localhost:3000` by default). `GET /v1/graph` returns the validated model and an ETag;
`PUT /v1/graph` requires that ETag, revalidates the complete graph, and atomically replaces the
configured file. A stale ETag or invalid graph leaves the file unchanged. The browser never sends
a filesystem path. Saving is model-lossless but may normalize YAML formatting and comments.

To validate that graph against one manifest pipeline, manually run its already-deployed Cloud Run
job, and read the latest execution plus Dander run-ledger result, bind the operator service at
startup:

```bash
dander graph serve \
  --file /absolute/path/to/graphs/greenhouse_jobs.yaml \
  --config /absolute/path/to/dander.yaml \
  --pipeline greenhouse_jobs_graph \
  --project my-gcp-project
```

The project, region, job name, pipeline, graph path, and revision are fixed by the operator and the
validated manifest; the browser cannot replace them. Run submission uses the installed `gcloud`
identity and is rejected while another execution is active. Status reads the existing run ledger
without creating or altering it.

This bridge edits `PipelineGraph` and can execute only the bound, already-deployed job. It does not
write `dander.yaml`, resolve secrets, deploy changes, enable schedules, or plan/apply Terraform.

To opt into candidate planning, supply the complete current platform inputs at service startup:

```bash
dander graph serve \
  --file /absolute/path/to/graphs/greenhouse_jobs.yaml \
  --config /absolute/path/to/dander.yaml \
  --pipeline greenhouse_jobs_graph \
  --project my-gcp-project \
  --enable-deployment-preview \
  --billing-account "$DANDER_BILLING_ACCOUNT" \
  --failure-alert-email "$DANDER_ALERT_EMAIL" \
  --secret-id OPTIONAL_EXTRA_MANAGED_SECRET
```

**Build candidate & plan** is separate from Save. It snapshots the exact saved ETag, builds and
pushes a source-free image from the public `dander-platform` version, and renders the full-manifest
Terraform plan. All cloud/plan inputs are fixed by the operator; the browser cannot replace them.
Repeat `--secret-id` for any Terraform-managed secret containers that are not referenced by a
manifest pipeline, so the full plan preserves them.
The binary plan is created in a temporary copy of `infra/`, deleted after rendering, and cannot be
used by `dander init-platform-apply`. No Terraform apply, scheduler change, or runtime deployment
occurs. Because one image is shared by the manifest, the preview names every hosted job that would
consume the candidate if a later, separately approved deployment were prepared.

## Connector-backed execution

A hosted pipeline may set `graph: graphs/<name>.yaml`, leave `models` empty, and set
`build_models: false`. Each graph source node binds to the pipeline's existing connector YAML:

```yaml
type: source
config:
  connector: greenhouse_job_board
  endpoint: jobs
```

`dander run <pipeline>` then extracts only the bound endpoints through the normal connector,
watermark, lease, and run-history path. The graph compiler reads those raw BigQuery relations and
publishes every configured `replace` target through run-scoped staging plus a transactionally
fenced `DELETE`/`INSERT` finalizer. The same command is used by the existing hosted Cloud Run job,
so the source-free image is also the deployment artifact.

## Scope boundary

This package combines declarative modeling with a bounded execution bridge. It does not:

- execute arbitrary expressions, Python code, or legacy edge-level `JoinSpec` joins;
- execute graph writer modes other than `replace`;
- execute graph field tests or publish graph metadata to Dataplex;
- combine multiple connector YAML files in one hosted graph pipeline.

`compile_target` parses allow-listed scalar expressions and emits explicit-column SQL for the
supported graph subset. `prepare_target_writer` constructs a concrete writer; only a later call to
that writer's `write()` method performs I/O. Ingestion, transform submission, hosted scheduling,
and write execution otherwise remain in their named modules.

Related tickets: `tickets/DANDER-2-pipeline-graph-model.md` (base `Node`/`Edge`/`PipelineGraph` +
serialization), `DANDER-3` (structural validation), `DANDER-4` (node field schema), `DANDER-5`
(field mapping), `DANDER-6` (transformations), `DANDER-7` (join spec), `DANDER-8` (field-wiring
validation), `DANDER-10` (typed per-node-type config), `DANDER-11` (source request/payload spec),
`DANDER-18` (node cursor / watermark strategy).
Relevant steering: `steering/00-project-overview.md`, `steering/01-security.md`,
`steering/02-engineering.md`.

## Decision Log status

DANDER-7 put declarative join semantics on graph edges. The later executable-join decision is
recorded in `docs/decisions.md`: executable joins belong to a distinct transform output node,
while the edge-level shape remains loadable but fails closed during compilation.

DANDER-18 put cursor strategy on the graph node (`Node.cursor`). Connector endpoint execution and
watermark persistence now bind through `config.connector`/`config.endpoint`; broader declarative
request execution remains outside this package.
