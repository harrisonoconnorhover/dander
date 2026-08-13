# Dander Control contract bundle

Status: the original bundle is published in Dander `0.9.0rc18`; the additive hosted-resource
revision on protected source is not yet published

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
344ef5ff2d685d5bedf7a1ddb119a42a6de08d90f285dc0a981e79c55452c1ed
```

That digest identifies the immutable public `0.9.0rc18` artifact. DANDER-121 adds the previously
missing project list, graph create/resource/page, and run page envelopes without changing existing
contract meanings. The resulting source digest is
`e88f732308db41872d0438b9b79df345647c4552a1c750e0230515939d09a246`. It remains unpublished
until a separately approved immutable release; Druff must continue consuming the public rc18
digest until that release exists.

The same digest is present in the immutable public `dander-platform==0.9.0rc18` wheel and source
distribution published from protected-main commit
`ae2f8f6bfda5fe54309c54eee623b83d0b2bd2a3` at tag `v0.9.0rc18`. The approved trusted-publishing
workflow is [run 31719571923](https://github.com/harrisonoconnorhover/dander/actions/runs/31719571923),
and the matching beta prerelease is
[Dander 0.9.0rc18](https://github.com/harrisonoconnorhover/dander/releases/tag/v0.9.0rc18).

The public artifact identities are:

- wheel `dander_platform-0.9.0rc18-py3-none-any.whl`:
  `sha256:4500b32451c02b6331a337b6d38eb96cc49a29838b6e3ea5a2b87b9daf85406c`;
- source distribution `dander_platform-0.9.0rc18.tar.gz`:
  `sha256:bf5ead721ab2b61eff4b50be5c3ab9cb03edb59257c0b2a3f1c0019c7045c3ae`.

A fresh PyPI-only install outside any checkout reported `dander 0.9.0rc18`, generated and
validated the source-free starter project, passed Terraform initialization and validation, and
independently matched all 25 installed contract files to the manifest hashes. This publication
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

The adapter's shared fake-provider conformance is implemented in DANDER-122. GCS remains
unqualified until the separately approved live restart/conflict/cleanup, bucket-policy, and
retained-infrastructure no-drift evidence passes.

These are server-internal storage semantics for DANDER-120. DANDER-121 projects them through the
separately named hosted service while preserving `dander graph serve --file` unchanged.

## Hosted Control service

`dander control serve` is distinct from the existing one-file bridge. It exposes configured
logical projects and multi-graph CRUD, validation, capability/catalog discovery, and normalized
preview/run routes over `GraphStore`. Until DANDER-126 adds OIDC authorization, the command rejects
every non-loopback bind. Unwired preview and lifecycle operations remain absent from capabilities
and fail closed instead of falling through to the GCP-specific local wrapper.

Opaque store revisions are base64url-wrapped in strong quoted HTTP ETags and decoded exactly for
conditional operations. Run start uses `If-Match` plus `Idempotency-Key` headers so the published
v1 `RunRequest` meaning remains unchanged. Create/cancel/replay likewise have one explicit
idempotency source, graph bodies are streamed only to a fixed limit, header-only mutations reject
bodies, list and log pages are bounded, oversized responses fail closed, and mutation audit
records contain only method, route template, status, and correlation ID. Response DTOs omit
provider payloads, credentials, secret values, SQL, rows, and raw exception messages.

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
