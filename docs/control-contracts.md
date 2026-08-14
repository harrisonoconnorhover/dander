# Dander Control contract bundle

Status: the complete hosted-resource bundle is published in Dander `0.9.0rc19` and is the
immutable release artifact Druff may consume

Dander is the authority for data crossing the future Control API boundary. The deterministic
`io.dander.control.contracts/v1` bundle lives in `src/dander/control/contracts/v1` and is included
in the `dander-platform` wheel and source distribution. Druff must generate its client from a
separately approved, immutable Dander release artifact; it must not copy these files from a source
checkout or treat handwritten browser schemas as authoritative.

## Contents and identity

The bundle contains Draft 2020-12 JSON Schemas and canonical fixtures for graph documents, API
errors, connectors, plugins, operations, deployment previews, graph validation, run requests and
status, bounded logs, mutation results, and capabilities. Each schema has a stable URN `$id` and
uses only self-contained `#/$defs` references.

`manifest.json` records every file hash and the bundle SHA-256. The bundle digest excludes the
manifest itself, so the same source models always produce the same identity. The current reviewed
digest is:

```text
695791dfda6058d68453d9e146146d5cdda1439d86c40a7ec249cb4e14a12be3
```

The DANDER-121 hosted-resource revision first added project list, graph create/resource/page, and
run page envelopes at intermediate source digest
`e88f732308db41872d0438b9b79df345647c4552a1c750e0230515939d09a246`. DANDER-126 then added the
secret-free Control bootstrap contract. The final digest above includes both revisions without
changing previously published contract meanings.

The final digest is present in the immutable public `dander-platform==0.9.0rc19` wheel and source
distribution published from protected-main commit
`cad383b8ac74e8ba0ce0b3b92c66b0a5a93a306b` at tag `v0.9.0rc19`. The approved trusted-publishing
workflow is [run 31785512985](https://github.com/harrisonoconnorhover/dander/actions/runs/31785512985),
and the matching beta prerelease is
[Dander 0.9.0rc19](https://github.com/harrisonoconnorhover/dander/releases/tag/v0.9.0rc19).

The public artifact identities are:

- wheel `dander_platform-0.9.0rc19-py3-none-any.whl`:
  `sha256:8f1336786e46471a2048d6250008ad176ff3b62d047020872659304c7d2db552`;
- source distribution `dander_platform-0.9.0rc19.tar.gz`:
  `sha256:d98063760209b2b310f4113fe44c0e65e1e788748f890e95fb91e944cb63b2db`.

A fresh PyPI-only install outside any checkout reported `dander 0.9.0rc19`, generated and
validated the source-free starter project, passed Terraform initialization and validation, and
independently matched all 37 installed contract files to the manifest hashes. This publication
record authorizes Druff to generate from the release artifact; it does not make a source checkout
an acceptable contract input.

## Boundary rules

- Explicit immutable transport DTOs describe JSON; Dander domain models remain semantic authority.
- Known graph node types use typed configuration and typed operation branches.
- Unknown extension node types and their JSON configuration are preserved without weakening known
  branches.
- `params` is accepted as a legacy node-config input alias and serialized canonically as `config`.
- Root objects, graph node shells, edges, and closed nested value objects such as writer settings
  reject undeclared fields. Known node `config` objects preserve extra JSON because the canonical
  node-config domain boundary preserves those extensions.
- Users may author `load_job`, `storage_write`, and `copy` writer transports. Provider-selected
  `direct` transport remains rejected as authored input.
- Provider extensions and JSON `null` values survive the domain/transport round trip.

Semantic rules such as graph wiring are still enforced by Dander. The schema is a transport
contract and must not become a second planner, warehouse, provider, or orchestration authority.

## Graph persistence boundary

The internal `GraphStore` port validates each document through `PipelineGraphDocument` and the
canonical domain serializer before persistence. Its portable identity is SHA-256 over one exact
encoding: UTF-8 JSON, sorted keys, compact separators, unescaped Unicode, no non-finite numbers,
and no trailing newline. Provider revisions remain separate opaque concurrency tokens.

List pages contain only document-free summaries and at most 100 entries. Full documents are
returned only by get/create/put. Create and delete idempotency keys are scoped by project and
operation; successful identical retries replay exactly, conflicting reuse fails, and failed
validation or preconditions do not consume a key. The initial rooted local adapter journals a
pending mutation before changing a graph and marks it complete afterward, so a restart at either
boundary is recoverable without arbitrary filesystem access.

The GCS adapter keeps the same contract behind one immutable bucket/prefix binding. It uses native
object generations for create, update, delete fencing, and opaque revisions; reads pin both the
observed generation and an explicit byte range; and pagination compensates for GCS's inclusive
`start_offset`. Healthy list entries come from validated safe object metadata and never download
full graph bodies. Hashed create/delete journals preserve exact restart replay without persisting
raw idempotency keys, and workers that race the same key converge on the durable winner. Exact
credential-bearing graph fields accept recognized secret references only, so the store never
becomes a credential-value repository. Importing Control remains provider-free; the Google SDK
loads only when this adapter is actually constructed without an injected client.

The adapter's shared fake-provider conformance and separately approved live
restart/conflict/replay/cleanup proof passed in DANDER-122. The
[coordinate-free evidence](evidence/gcp/2026-08-13/druff-gcs-graph-store.json) also records the
required bucket policy, removal of all object versions and the bucket, and retained-infrastructure
no drift. This qualifies protected-main source commit `81e750f`; public rc18 predates the adapter
and is not qualified by this proof.

The S3 adapter keeps that same public contract behind one explicitly bound general-purpose bucket
and deterministic prefix. Exact quoted ETags are opaque revisions: conditional puts own creates,
replacements, fences, and journal transitions, while conditional reads and deletes reject stale
objects. Native exclusive `StartAfter` pagination plus validated `HeadObject` metadata produces
healthy summaries without downloading graph bodies. Bounded range reads close their streams, and
404/409/412 responses are interpreted by operation so a read absence cannot masquerade as a lost
conditional mutation race. Directory buckets remain unsupported because they do not provide the
ordered `StartAfter` contract. DANDER-123 remains in progress until a separately approved live AWS
restart/conflict/versioning/cleanup proof passes; neither the current source nor public rc18 is
live-qualified for S3.

The Azure Blob adapter keeps the same public contract behind one HTTPS storage-account endpoint,
container, and deterministic prefix. Creates rely on native absence semantics and every later
read, replacement, fence transition, and deletion pins an exact opaque Blob ETag with
`IfNotModified`. Inclusive `start_from` pages follow native continuation tokens and carry validated
metadata, so healthy listing does not issue per-blob reads or download documents. The adapter
deletes only the exact current base blob: snapshots and versions are never silently expanded into
the deletion scope, and a snapshots-present or immutability/lease policy failure remains a safe
provider error rather than a false revision conflict. SDK imports and `DefaultAzureCredential`
remain lazy. DANDER-124 remains in progress until separately approved live Azure
restart/conflict/versioning/cleanup proof passes; public rc18 is not qualified for this adapter.

The OCI Object Storage adapter keeps the same public contract behind one immutable namespace,
bucket, and deterministic prefix. Default construction uses only resource-principal identity;
profile or security-token authentication requires an explicitly injected client. Creates use
native absence matching, while reads, replacements, delete fences, journal transitions, and
deletes pin exact opaque ETags. Public cursor resumes use exclusive `start_after`; internal
provider pages use OCI's returned `nextStartWith` as an inclusive `start`. Because list summaries
do not contain object metadata, each candidate receives a bounded HEAD request, but healthy list
traversal never downloads graph bodies. Reads reject an oversized HEAD before GET, request at most
one bounded byte range, and always close the OCI stream.

Deletes target only the exact current object and never pass a version ID or enumerate history. In
a versioned bucket, OCI therefore retains older versions and creates a delete marker. OCI reports
the same `NotAuthorizedOrNotFound` code for object absence and some authorization failures; the
adapter treats that exact response as absence only at an object-addressed HEAD boundary, maps a
post-observation disappearance to a conflict, and keeps list/bucket errors fail-closed. This
provider limitation is explicit. DANDER-125 remains in progress until a separately approved live
OCI policy/restart/conflict/versioning/cleanup/no-drift proof passes; public rc18 is not qualified
for this adapter.

Phase D3's exit gate is satisfied on protected main commit
`edf0ee3f473839a10f5eb53710636c95c2f5bd64`. The same provider-neutral conformance suite passes
for the in-memory, rooted-local, GCS, S3, Azure Blob, and OCI Object Storage implementations, and
the accepted [GCS live proof](evidence/gcp/2026-08-13/druff-gcs-graph-store.json) supplies the
gate's required one live create/read/update-conflict/restart/delete demonstration. DANDER-123,
DANDER-124, and DANDER-125 remain in progress: their AWS, Azure, and OCI live proofs still gate
promotion of those providers, but the D3 gate does not require false all-provider live parity
before hosted authentication work begins.

These are server-internal storage semantics for DANDER-120. DANDER-121 projects them through the
separately named hosted service while preserving `dander graph serve --file` unchanged.

## Hosted Control service

`dander control serve` is distinct from the existing one-file bridge. It exposes configured
logical projects and multi-graph CRUD, validation, capability/catalog discovery, and normalized
preview/run routes over `GraphStore`. Loopback mode remains unauthenticated and needs no identity
configuration. Every external bind requires a valid non-secret `--oidc-config` input; hosted `/v1`
routes then reject missing or invalid access tokens before application dispatch. Unwired preview
and lifecycle operations remain absent from capabilities and fail closed instead of falling
through to the GCP-specific local wrapper.

Opaque store revisions are base64url-wrapped in strong quoted HTTP ETags and decoded exactly for
conditional operations. Run start uses `If-Match` plus `Idempotency-Key` headers so the published
v1 `RunRequest` meaning remains unchanged. Create/cancel/replay likewise have one explicit
idempotency source, graph bodies are streamed only to a fixed limit, header-only mutations reject
bodies, list and log pages are bounded, oversized responses fail closed, and mutation audit
records contain only method, route template, status, and correlation ID. Response DTOs omit
provider payloads, credentials, secret values, SQL, rows, and raw exception messages.

## Hosted OIDC boundary

`HostedOIDCDeploymentInput` is the single immutable source for the API trust settings, exact CORS
origins, public SPA registration, and secret-free `control-bootstrap` contract. The public client
and API audience must differ. The client projection permits only authorization code, response code,
PKCE S256, and no client secret; it does not grant browser refresh tokens. Startup verifies that
issuer, audience, client ID, redirect URI, logout URI, and origins agree across projections. The
bootstrap includes the current Control contract identity and Druff compatibility range.

Dander accepts bearer access tokens only in the `Authorization` header. It validates a fixed
asymmetric algorithm allow-list, signature, exact issuer, API audience, expiry, subject, and roles.
The bounded JWKS resolver uses a fixed HTTPS URI, timeout, response/key-count limits, one shared
unknown-key refresh cooldown, and single-flight refresh while retaining the last good cache on a
fetch failure. Optional exact subject/email/group allowlists remain deployment configuration;
email allowlisting also requires `email_verified` to be the Boolean value `true`.

Roles map centrally to five server-enforced capabilities: viewer can read; editor adds graph edit
and validate/preview; operator adds run/cancel/replay; admin adds graph deletion/administration.
The capability response is filtered to the authenticated role. Human claims never become provider
workload credentials.

Hosted CORS permits only exact configured HTTPS origins, no credentialed browser requests, and an
explicit method/header set. It exposes only `ETag` and `X-Correlation-ID`. Hosted responses apply a
deny-by-default CSP and standard no-store, framing, MIME-sniffing, referrer, permissions headers.
Tokens in query parameters are rejected before routing. Uvicorn access logging is disabled because
its default format includes query strings; any front proxy must likewise omit query strings from
access logs. Dander keeps no human cookie or server session, so cookie CSRF controls and session
cookies are intentionally inapplicable. Druff must keep access tokens in memory, use session
storage only for the short PKCE/state/nonce transaction, and reauthenticate after expiry; it must
not use URL tokens, browser cloud credentials, localStorage, or a client secret.

## Service deployment boundary

`ResolvedControlServiceRequest` is the immutable provider-neutral input for a long-running hosted
Control service. It carries the exact Dander image digest, derived external `control serve`
command, port and probes, resources and scaling, shutdown deadline, non-secret environment,
typed secret references, workload identity, ingress visibility, GraphStore locator,
observability, and accepted rollback digest. Its exact CORS origins and OIDC configuration are
derived from the existing frozen `HostedOIDCDeploymentInput`; a second trust or origin source is
not permitted.

GraphStore configuration is a closed credential-free union: rooted local, GCS bucket/prefix, S3
bucket/prefix/owner, Azure HTTPS account/container/prefix, or OCI namespace/bucket/prefix. It
contains no clients, credentials, IAM policy, network IDs, or provider extension mapping. Service
providers must render the selected binding's deterministic JSON at the request's exact
`graph_store_config_path`; the derived command always passes that path to `control serve`.
Startup parses the bounded closed schema before adapter access and instantiates only the named
local/GCS/S3/Azure/OCI adapter, so a declared cloud binding cannot silently fall back to local
disk. Provider networking, TLS, IAM, native resource IDs, and config delivery remain inside the
D7 provider modules. Existing launcher requests, templates, and provider projections are a
separate unchanged boundary.

`StaticAssetBundle` separately identifies Druff's static/OCI digest, entrypoint, bootstrap path
and digest, and required security headers. It is not a job template or a Dander service template.
Both service and static projections normalize unordered pairs before deterministic serialization.

The service projection, typed GraphStore startup seam, and local renderer are published in
immutable `dander-platform==0.9.0rc20` from protected-main commit
`75c5654e95439eaf18e90fbacc849799f4fe42b6` at tag `v0.9.0rc20`. Trusted-publishing
[run 31815063258](https://github.com/harrisonoconnorhover/dander/actions/runs/31815063258)
produced wheel
`sha256:754d255c4d9debf2e85cd8a008b79876758555eb51bacf090fdb7420b10d3992` and source
distribution `sha256:aaa7c986c78fe8eff47fdc5a7804d2ea832c59a7cbad1965b8d44b40233edf04`;
their hashes and sizes matched PyPI. A fresh no-cache PyPI-only install outside every checkout
reported `dander 0.9.0rc20`, imported from `site-packages`, generated and validated a project, and
passed Terraform initialization and validation. The matching beta prerelease is
[Dander 0.9.0rc20](https://github.com/harrisonoconnorhover/dander/releases/tag/v0.9.0rc20).

The first D7 renderer is the local Compose profile. It consumes one closed immutable non-secret
input, reuses the D6 service/OIDC/GraphStore projections, and emits exact active/rollback image
environments plus aligned Control and Druff JSON. Compose has no build path and publishes only a
localhost HTTPS edge. A networkless one-shot initializer receives only `CHOWN` and `FOWNER` so it
can set the named local GraphStore volume's owner and mode for UID/GID 65532; every long-running
container is non-root, read-only, and capability-free. Local verification uses repeatable Compose
rendering, stable second-up container identities, restart persistence, digest rollback/restore,
and exact disposable cleanup. Terraform
state, saved plans, provider workload identity, and cloud cost ceilings do not apply locally.

The local profile passed live qualification on 2026-08-14 using exact locally loaded active and
rollback Dander/Druff digests. One synthetic OIDC code/PKCE journey proved separate API and SPA
audiences, RS256, bounded expiry, admin authorization, and no refresh token. API- and
browser-created graphs survived a Control restart, digest rollback, and restoration; repeated
Compose renders were byte-equal and the unchanged second `up` preserved all running service IDs.
All disposable services, registry copies, network, GraphStore volume, generated projections, and
TLS material were removed, followed by fresh retained-GCP stage-zero and current-equivalent
platform `No changes` plans. The sanitized record is
`docs/evidence/local/2026-08-14/d7-control-plane.json`. This qualifies neither a real identity
provider nor any Kubernetes or cloud-hosted Control profile.

## Regenerate and verify

After an intentional DTO change, regenerate the committed bundle:

```bash
uv run python scripts/generate_control_contracts.py
```

Verify that committed artifacts are current and independently valid:

```bash
uv run python scripts/check_control_contracts.py
uv run pytest -q tests/control/test_contract_bundle.py tests/control/test_contract_models.py
```

CI rejects drift. Distribution checks also require the manifest and graph schema in both the wheel
and source distribution. Credentials, provider rows, state, plans, and live evidence never belong
in this bundle.
