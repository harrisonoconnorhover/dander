# Druff 1.0 control-plane architecture checkpoint

Status: Phase D0 accepted for implementation planning; documentation only

Date: 2026-08-13

This checkpoint evaluates the proposed Druff 1.0 self-hosted control plane against the exact
Dander and Druff repositories after Dander Phase 7. It is separate from Dander Phase 8. It does
not start a control service, publish an artifact, register an OIDC client, mutate a provider, or
change any application behavior.

## Baseline and entry gate

| Item | Recorded baseline |
|---|---|
| Dander origin | `https://github.com/harrisonoconnorhover/dander.git` |
| Dander upstream | `https://github.com/WagnerJ-Dev/dander.git` |
| Dander commit | `536b31b701a67a5b7eeb68e09e1d87a4c59898f9` |
| Dander protected-main CI | GitHub Actions run `31704251539`, successful at the exact commit |
| Dander main governance | Active `Protect main` ruleset `20133128`: PR, required Python/Terraform/secret/container checks, no force push or deletion |
| Druff origin | `https://github.com/harrisonoconnorhover/druff.git` |
| Druff upstream | `https://github.com/WagnerJ-Dev/druff.git` |
| Druff commit | `f9cc23cd8763b7cf761a61ba6be9ad9c26b42d96` |
| Druff main CI | GitHub Actions run `31050509937`, successful at the exact commit |
| Druff main governance | No classic protection or repository ruleset at assessment time; protection is required before D1 implementation may merge |

The Phase 7 entry gate is satisfied. `docs/cloud-portability-oci-lifecycle-acceptance.md` and
`docs/evidence/oci/2026-08-13/phase7.json` record public `dander-platform==0.9.0rc17`, runtime
index `sha256:190e9caa082efcd72e9a2a586c082c266e48f99a0bb69b99e30114e3c8c886b9`, zero
non-deleted OCI Container Instances, an inactive schedule, OCI stage-zero/foundation no drift,
and retained-GCP stage-zero/platform no drift. Obsolete local Phase 7 plans and state were removed
after the unique non-secret reproduction inputs were retained in operator-local sanitized
manifests. No plan, state, credential, provider row, or raw log is part of this checkpoint.

The unrelated Phase 8 qualification draft is checkpointed locally at
`eec57a89c3d302d82b40d15ebc468c2858d1a3d1`. It is not pushed, merged, or consumed here. Druff
does not become a Phase 8 exit criterion.

## Current capability inventory

### Canonical Dander foundations

| Capability | Current authority and evidence | D0 disposition |
|---|---|---|
| Graph shape and stable serialization | `src/dander/pipeline/graph.py` and `node_config.py` | Keep Dander authoritative; publish an explicit transport contract in D1. |
| Graph semantics | `src/dander/pipeline/graph_ops.py` | Reuse server-side; Druff validation remains advisory. |
| Provider-neutral graph compilation | `src/dander/pipeline/compiler.py` and `runtime.py` | Reuse after project/profile resolution; do not move SQL or planning into Druff. |
| Connector registry | `src/dander/plugins/contracts.py` and `registry.py` | Project presentation-safe descriptors through the Control API. |
| Curated plugin catalog | `src/dander/plugins/catalog.py` | Reuse content; replace the handwritten HTTP shape with a versioned transport DTO. |
| Operation catalog | `src/dander/pipeline/operations.py` | Reuse definitions; publish a versioned descriptor DTO. |
| Run history | `src/dander/state/run_history.py` and provider state runtimes | Reuse the non-sensitive aggregate model behind normalized Control API run DTOs. |
| Provider composition | `src/dander/providers/registry.py` (`PROVIDER_API_VERSION = 1`) | Internal Dander boundary only; never expose the registry to Druff. |
| Logical/deployment configuration | logical project v2 and platforms manifest v1 in `project/portable_config.py` | Resolve inside Dander; expose only capabilities and safe selected-profile diagnostics. |
| Job projection | `ResolvedTemplateRequest`, `ExecutionTemplateFactory`, and `ExecutionTemplate` | Preserve unchanged for jobs; do not force HTTP services into it. |
| Runtime/artifact contracts | `io.dander.runtime/v1`, execution projection v1, compatibility/capability manifests, artifact v1 | Reuse immutable identity and compatibility conventions. |
| Infrastructure and verification | GCP, AWS, Azure, OCI Terraform roots, Kubernetes chart, and provider verifiers | Extend provider-by-provider only after local API, store, auth, and service contracts pass. |

### Existing local Graph API

`src/dander/pipeline/graph_service.py` exposes these exact loopback routes:

| Method and path | Current behavior | Reuse assessment |
|---|---|---|
| `GET /v1/graph` | Load one selected graph and return an ETag. | Reuse validation/serialization semantics, not the implicit path. |
| `PUT /v1/graph` | Validate and conditionally replace that file. | Reuse optimistic-concurrency semantics behind `GraphStore`. |
| `GET /v1/connectors` | Project installed connector descriptors. | Reuse content behind a typed transport DTO. |
| `GET /v1/plugin-catalog` | Return curated package metadata. | Reuse content behind a typed transport DTO. |
| `GET /v1/operations` | Return presentation metadata for supported operations. | Reuse content behind a typed transport DTO. |
| `GET /v1/graph/status` | Return one GCP binding, Cloud Run execution, and BigQuery run record. | Local/GCP wrapper; do not adopt as the hosted run model. |
| `POST /v1/graph/validate` | Recheck revision, graph, and fixed manifest binding. | Extract the durable validation application operation. |
| `POST /v1/graph/run` | Invoke one fixed Cloud Run Job with local `gcloud`. | Local/GCP wrapper; replace with a provider-neutral lifecycle port. |
| `POST /v1/graph/deployment-preview` | Push a GCP candidate and render a temporary Terraform plan. | GCP/project-filesystem workflow; define a normalized plan application port before hosted reuse. |

The local service already has useful safety properties: loopback binding, exact origin, one
operator-selected file, a 5 MiB body limit, strict request paths, conditional saves, atomic
replacement, and sanitized validation details. Local mode remains supported as-is.

### Current Druff foundations and drift

Druff is already a static Next.js export (`next.config.ts` sets `output: "export"`). Its only
page is a client application; there are no route handlers, middleware, cookies/headers APIs,
server-only environment reads, or other required Next.js runtime behavior. The production image
contains compiled static assets and BusyBox, not Node or repository source.

Druff has useful seams for graph persistence, connector/plugin/operation discovery, and graph
operations. It also has explicit open/save conflict state, import/export, canvas conversion,
inline validation, deployment-preview controls, and focused unit and Playwright coverage. Those
seams should be adapted to one generated Control API client rather than replaced with cloud
clients.

The current cross-repository schema is not authoritative:

- `src/lib/pipeline-graph/schema.ts` manually mirrors Dander and its strict `NodeFieldSchema`
  omits Dander's `extensions: tuple[ProviderExtension, ...]`; a current Dander graph using that
  field is rejected by Druff.
- Druff structurally accepts some trigger, test, transformation, and writer combinations that
  Dander rejects semantically. This is acceptable only as advisory editing; Dander must remain
  the enforcing boundary.
- Druff's writer transport enum knows `load_job` and `storage_write`; Dander also models `copy`
  and provider-selected `direct`, with direct authoring rejected by Dander. Hand synchronization
  cannot safely express those distinctions.
- Typed source/transform/target configuration is reduced to `record<string, unknown>` in Druff.
  Conversely, `PipelineGraph.model_json_schema()` currently reduces Dander's validator-routed
  `Node.config` to an open object/empty `NodeConfig` and does not encode the per-call
  `extra="forbid"` boundary.
- Connector, plugin, operation, deployment-preview, and graph-operation responses are separate
  handwritten Zod contracts.
- `graph-validation.ts` is a deliberate manual TypeScript port. It is useful for immediate UI
  feedback but is not a second semantic authority and may not authorize a mutation.

## Required-question disposition

### 1. Which Dander API operations already exist and can be reused?

Canonical load/validate/save behavior; presentation-safe connector, plugin, and operation
catalog construction; graph compilation; run-history reads; and the revision checks are reusable.
The exact current routes are inventoried above. The hosted API should call extracted application
services that use those domain functions, not make HTTP-to-HTTP calls into the loopback handler.

### 2. Which endpoints are local-only wrappers around durable domain operations?

`GET/PUT /v1/graph` wrap canonical graph parsing, validation, serialization, and conditional
persistence but bind them to one local file. The catalog routes wrap durable catalog builders but
hand-project their response shapes. Validate wraps durable graph validation plus a local fixed
binding. Status/run/deployment-preview are not provider-neutral domain endpoints: they directly
bind GCP project IDs, Cloud Run Jobs, BigQuery run history, local `gcloud`, a filesystem project,
candidate publication, and GCP Terraform.

### 3. Which parts improperly depend on a filesystem path or one graph file?

`GraphDocumentStore` requires one existing `.yaml/.yml/.json` path at construction. The server
has no project/graph address, create/list/delete operation, or persistence root. `GraphOperationBinding`
fixes graph and project configuration paths at startup. `GraphDeploymentPreviewer` fingerprints
and copies `connectors`, `graphs`, `models`, `infra`, `dander.yaml`, and
`dander.platforms.yaml` from one checkout. Those are appropriate local boundaries but cannot be
the hosted persistence or provider-neutral deployment API.

### 4. Which Druff schemas have drifted from Dander?

The concrete drift is listed in the preceding section: missing field extensions, manual node
configuration, incomplete writer-transport knowledge, semantic validators that differ by design,
and handwritten catalog/operation/response shapes. D1 must remove authoritative manual schemas,
not merely regenerate TypeScript from today's incomplete `PipelineGraph.model_json_schema()`.

### 5. Can Druff remain a static export without removing required behavior?

Yes. Graph CRUD, validation, catalogs, preview, run controls, polling, and OIDC authorization-code
with PKCE are browser-to-Control-API interactions. None requires a Next.js server. The existing
static build is the smallest secure topology and preserves one cloud-ignorant artifact.

### 6. Which exact feature requires a Next.js server?

None at this checkpoint. A future cookie-session/BFF requirement could justify a server, but it is
not a current Druff 1.0 requirement. Adding a server now would duplicate the Dander API boundary
and create a second authenticated service without product value.

### 7. What is the smallest secure OIDC topology?

Use one external issuer and a public SPA client with authorization code plus PKCE. The public
bootstrap descriptor contains only API URL, issuer, client ID, audience, exact redirect/logout
URI, and contract compatibility metadata. PKCE verifier, state, and nonce exist only for the
short callback transaction in session storage; the access token exists only in memory. Do not use
localStorage, URL tokens, a browser client secret, or a browser refresh token. At token expiry,
perform a bounded reauthentication.

Dander validates signature, issuer, audience, expiry, and required claims on every hosted request,
then applies centralized role-to-capability authorization. The browser's UI checks are only
presentation. Local loopback mode stays unauthenticated and physically separate. Exact CORS
origins, OIDC callback configuration, and the public descriptor are generated from one typed
deployment input. The descriptor is discovery data, never the CORS or trust authority; deployment
verification fails if the public and server-side projections differ.

### 8. Which launcher/provider contracts can be reused?

Reuse internal provider registry/factory mechanics, lazy SDK loading, selected platform/profile
resolution, immutable artifact identity, normalized runtime events, compatibility/capability
manifests, and each provider's existing operations implementation where its behavior fits a new
application port. Do not expose the registry to Druff. Do not reuse the job template as an HTTP
service template. Existing Fargate, Azure, and OCI operations have similar start/latest/describe/
logs/cancel/replay behavior but different types and identifiers; Cloud Run graph operations are a
separate GCP wrapper, and Kubernetes currently plans/verifies jobs rather than operating runs.
They need a normalized Dander lifecycle adapter, not React branches or a universal orchestration
framework.

### 9. What exact new service-deployment contract is required?

Add an internal service provider kind and one immutable `ResolvedControlServiceRequest` adjacent
to the launcher request. It contains only:

- service ID and selected deployment/profile ID;
- the existing immutable Dander image and the `control serve` command;
- listening port plus liveness and readiness paths;
- CPU, memory, minimum/maximum instances, and graceful-shutdown deadline;
- non-secret environment values and typed secret references;
- workload identity;
- ingress visibility and exact allowed origins;
- graph-store binding by typed provider configuration, never credentials;
- log destination, alert target, and retention; and
- accepted/previous digest for deterministic rollback verification.

`ControlServiceTemplateFactory.build(request)` returns a deterministic provider-specific
`ControlServiceTemplate`. Provider networking, TLS, IAM, load balancers, and native identifiers
stay inside provider modules. Existing job launchers remain unchanged.

Druff's `StaticAssetBundle` is a separate deployment input: content/OCI digest, entrypoint,
bootstrap descriptor path and digest, and required security headers. It may be served by a
provider static host or the existing source-free static image, but it is not forced into the job
launcher or Dander control-service contract.

### 10. Which Phase 8 interfaces can be frozen before Druff consumes them?

The actual reusable boundaries are logical project v2, platforms manifest v1, provider registry
API v1 internally, runtime contract v1, execution projection v1, compatibility/capability
manifests, and artifact identity/promotion. Druff consumes only the public Control API
capabilities/contract bundle, never the provider registry or platform manifests directly.

The local unpushed Phase 8 draft is excluded. Druff phases may use merged stable interfaces but
must not require Phase 8 qualification to finish, change Phase 8 evidence, or become a Phase 8
blocker. The Control API, GraphStore, OIDC, static artifact, and service contracts are new Druff
roadmap work.

## Final bounded architecture

```text
external OIDC issuer
        |
        | authorization code + PKCE
        v
static Druff artifact ---- Bearer token ----> Dander Control API
        |                                      |
        | generated client                     +-- graph/application services
        |                                      +-- GraphStore port
        |                                      +-- lifecycle/preview ports
        |                                      +-- provider registry (internal)
        v                                      v
no provider SDKs                     local/GCS/S3/Blob/OCI + selected providers
```

Dander owns every semantic and privileged operation. Druff knows one versioned API and generated
contracts. Human OIDC and cloud workload identity do not intersect. The deployment remains a
bounded single installation with one or more named graph documents; it does not become SaaS.

## Contract, API, and revision strategy

### Dander-owned transport bundle

D1 publishes `io.dander.control.contracts/v1` as a deterministic installed/release artifact. The
bundle uses explicit transport Pydantic DTOs where domain JSON Schema is incomplete. In
particular, the graph transport schema encodes:

- strict top-level graph/node/edge boundary behavior;
- type/config branches for source, transform, and target nodes;
- the intentionally extensible fallback for unmodeled node types and allowed typed-config extras;
- stable canonical omission and alias rules; and
- provider-extension and writer-transport shapes.

Domain models remain the semantic source, but generated transport schema is not claimed to be a
verbatim `model_json_schema()` dump. Cross-contract fixtures prove domain-to-transport validation,
canonical serialization, and round trip. The bundle also includes validation-error, catalogs,
preview, run/status/log/cancel/replay, error-envelope, capability, and compatibility DTOs. Files
are sorted/canonicalized and recorded with one SHA-256 digest. Druff generates types and runtime
validators from this exact artifact, and both repositories fail CI on drift.

### Versioning

- Additive optional fields remain compatible inside `v1` only where the schema permits them.
- Removing, renaming, changing meaning, or tightening an accepted field requires a new major
  contract/API version or an explicit compatibility window.
- `GET /v1/capabilities` advertises API version, contract bundle ID/digest, Dander version,
  supported operations, selected-profile limits, and minimum/maximum compatible Druff contract.
- Druff embeds its generated bundle ID/digest and fails with an actionable upgrade message when
  the server's range excludes it.
- Cross-repository changes land Dander producer, protected Dander release/artifact, then Druff
  consumer. Druff never consumes an unmerged checkout.

### Graph revisions

Hosted responses expose two separate values:

- an opaque quoted ETag used only for conditional concurrency; and
- `content_sha256`, computed over canonical JSON, used for portable identity/evidence.

Local filesystem ETags may continue to be content hashes. Provider generations, versions, and
ETags remain opaque inside adapters and are never compared cross-cloud. This keeps stale-write
rejection correct without pretending provider revision tokens are portable graph identities.

## Graph storage strategy and sequencing

The GraphStore port must precede or land with hosted multi-graph routing; the hosted API must not
first hard-code another one-file store. The semantic contract is:

```text
list(project, cursor, limit)
get(project, graph)
create(project, graph, canonical_document, idempotency_key)
put(project, graph, canonical_document, expected_revision)
delete(project, graph, expected_revision, idempotency_key)
```

Use strict portable identifiers and deterministic object names. The return envelope contains the
document, opaque revision, content SHA-256, and safe timestamps/metadata. D2 begins with in-memory
and rooted local-filesystem adapters plus the conformance suite; D3 adds GCS, S3, Azure Blob, and
OCI Object Storage in separate PRs. Adapters use native conditional create/update/delete controls,
bounded list pagination, provider-default encryption, and versioning/deletion protection where
configured. No arbitrary paths, credentials, business rows, or PostgreSQL graph database are
introduced.

## Compatibility and migration

- Preserve `dander graph serve --file GRAPH` and the current Druff local workflow throughout.
- Add a separately named hosted command such as `dander control serve`; do not widen the loopback
  command to a public bind.
- Adapt Druff's existing `GraphPersistence`, discovery, and operations seams to the generated
  client. Preserve import/export and detached local drafts.
- Treat the existing GCP status/run/preview API as local compatibility behavior. Hosted mode uses
  new normalized ports and advertises unsupported launcher operations explicitly.
- Existing graph files load through the same canonical models. The first hosted local-store
  migration imports only explicitly selected graph roots, records content hashes, and never
  traverses arbitrary filesystem paths.
- No support status changes occur merely because an adapter or Terraform module exists.

## Protected PR sequence

1. Paired D0 roadmap/baseline PRs (documentation only).
2. Dander explicit transport-contract producer and deterministic artifact.
3. Protected Dander release/artifact containing that producer.
4. Druff generated contract/types/validators and drift CI.
5. Dander GraphStore port plus in-memory/rooted-local implementations and conformance.
6. Dander hosted Control API over that port, preserving loopback mode.
7. GCS, S3, Azure Blob, and OCI Object Storage adapters, one PR each.
8. Dander hosted OIDC/authz boundary.
9. Druff static OIDC/bootstrap and generated Control API client integration.
10. Druff remote graph management, then validation/catalog/preview, then run controls in focused
    PRs.
11. Druff deterministic static artifact hardening and full local Playwright journey.
12. Dander control-service projection and static-site deployment input.
13. Local/Kubernetes deployment, then GCP, AWS, Azure, and OCI Terraform in separate PRs.
14. Separately approved provider live evidence, followed by cross-cloud evidence and release docs.

Druff main protection is a gate before step 4 may merge. Each substantial PR includes focused
tests, repository-wide required checks, secret/artifact scanning, decision/handoff updates, and
independent completion review.

## No-cost and paid gates

Phases D0 through the local portions of D6 are credential-free and no-cost. Unit, adapter-mock,
in-memory/local-store conformance, generated-contract drift, local OIDC test-issuer, container,
and Playwright work must pass before any provider deployment.

OIDC client registration is a human action and stops for explicit approval. Artifact publication,
public endpoints, provider registration, saved Terraform apply, or any paid proof also stops for:

1. the exact reviewed command or saved plan;
2. the provider and resource inventory;
3. an explicit numeric per-attempt ceiling;
4. the stable approval reference;
5. no automatic paid rerun; and
6. exact cleanup or retained no-drift verification.

Provider-generated HTTPS URLs are sufficient for initial acceptance. Public release, support
promotion, and custom-domain work require separate approval.

## Explicit non-goals

No organizations/workspaces, billing, password database, fine-grained enterprise RBAC,
multiplayer/CRDTs, comments, approval flows, environment promotion, secret UI, browser plugin
installation, browser execution, provider clients in Druff, arbitrary filesystem access,
universal asset model, WebSocket requirement, HA/multi-region service, generalized orchestration,
or generic platform framework is added. Druff does not alter Dander graph/pipeline semantics.

## D0 review disposition

The independent adversarial review initially blocked naïve use of Pydantic JSON Schema, an
independently trusted public bootstrap file, an incorrect platform-manifest version, and a
multi-graph API that preceded its storage port. This roadmap incorporates the minimum
corrections: explicit transport DTOs/fixtures, one typed deployment source for public and server
trust configuration, actual logical-project/platform versions, and GraphStore-first sequencing
with separate ETag and content identity.

With those corrections, the architecture confirms the requested north star: Dander semantic
ownership, cloud-ignorant static Druff, generated contracts, object-storage persistence, external
OIDC, separate service semantics, and provider-neutral acceptance. No material architectural
divergence requires a new product decision. Implementation may proceed only through the protected
sequence and gates above.
