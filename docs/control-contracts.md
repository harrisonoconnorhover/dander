# Dander Control contract bundle

Status: packaged and release-ready; not yet published in a Dander release

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

This digest identifies the repository bundle only. It is not evidence that a release was
published. Publication remains a separate protected-release action requiring explicit approval.

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
