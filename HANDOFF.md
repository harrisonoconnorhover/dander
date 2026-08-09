# Morning Handoff

## Finished

- Removed the BigQuery-derived namespace fallback from the provider-neutral warehouse coordinate
  protocol; each provider now accepts only canonical compatibility inputs.
- Kept BigQuery namespace precedence exactly `--dataset`, profile `warehouse.dataset`, then
  `BQ_DATASET_RAW`, resolved before neutral orchestration.
- Made the endpoint `RelationRef` map authoritative through CLI composition and
  `PipelineExecutor`, including complete-map and shared catalog/namespace validation.
- Removed unused internal `project`, `dataset`, `metadata_dataset`, and `warehouse_catalog` aliases
  while retaining public v1/CLI compatibility entry points.

## Try It

Run `uv run pytest -q tests/cli/test_run_command.py tests/test_executor.py` to exercise compatibility
translation and canonical relation flow without contacting a warehouse.

## Checks

- Ruff passed; strict mypy passed across 303 source files.
- The full suite passed: 1,100 tests with an ephemeral pinned PostgreSQL 15 service.
- Wheel, sdist, outside-checkout source-free installs, runtime-all install, generated-project
  validation, dependency audit, and non-root/read-only container conformance passed.
- GCP/AWS Terraform validation and tests plus Helm lint/template passed.

## Decisions

- Canonical endpoint relations are the single warehouse-location authority once CLI compatibility
  inputs have been translated.
- Mixed catalogs or raw namespaces fail before execution instead of selecting one relation
  implicitly.
- Provider registry, capabilities, fencing, schema contracts, v1 resources, and Terraform remain
  unchanged.

## Remaining

- Let protected CI repeat Linux packaging, image scanning, and secret scanning before merge.
- Keep the separate warehouse-capability worktree and future provider work out of this slice.
- Do not deploy, apply Terraform, publish packages, or expand provider support from this branch.

## Review First

- `src/dander/cli/run_command.py`
- `src/dander/executor.py`
- `src/dander/warehouse/contracts.py`
