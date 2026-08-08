# Morning Handoff

## Finished

- Added a typed `CatalogRuntime` with explicit publication capabilities.
- Registered Dataplex and `none` through dependency-light, lazy provider factories.
- Routed all CLI Dataplex publisher construction through the selected provider boundary.
- Preserved current aspect-only updates, required-schema exclusion, and normalized readback.
- Kept local registry and state snapshots independent; `none` loads no Dataplex implementation.

## Try It

Run an existing v1 project or v2 `catalog.provider: dataplex` project normally. Select
`catalog.provider: none` to keep local metadata without external catalog publication.

## Checks

- All 911 tests, Ruff, formatting, and strict mypy across 221 files passed.
- Wheel/sdist inspection, source-free installs, runtime-all assembly, and dependency audit passed.
- The non-root/read-only full runtime image passed conformance and bundled-asset checks.
- Trivy found no high/critical findings, Gitleaks found no leaks, and all Terraform roots validated.
- Isolated GCP reported `No changes`; Salesforce and ServiceNow schedules remain paused.

## Decisions

- Treat local registry/state snapshots separately from optional external catalog publication.
- Preserve Dataplex public imports lazily for compatibility.
- Defer canonical non-BigQuery catalog assets and Glue to their dedicated slices.

## Remaining

- Open and merge the focused Dataplex provider PR after protected CI passes.
- Route GCP Secret Manager through the provider boundary next.

## Review First

- `src/dander/catalog/runtime.py`
- `src/dander/providers/dataplex/runtime.py`
- `src/dander/cli/provider_runtime.py`
