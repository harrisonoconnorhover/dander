# Morning Handoff

## Finished

- Added replace, SCD2, snapshot, and incremental ingestion to Redshift's existing bounded COPY
  writer while preserving the SCD1 constructor.
- Kept every target mutation, complete-manifest replay record, and destination-fence touch in one
  Redshift transaction.
- Preserved whole-stream semantics for replace and SCD2 and deterministic cross-batch behavior for
  SCD1, snapshot, and incremental writes.
- Updated the exact runtime capability report and experimental Redshift documentation.

## Try It

Run `uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py tests/test_compatibility.py`.

## Checks

- Focused Redshift and compatibility tests passed: 33 tests.
- Ruff passed across 328 files, strict mypy passed across 304 source/test files, and full pytest
  passed with 1,133 tests and 13 environment-dependent skips.
- Wheel/sdist inspection and source-free installation, generation, and validation passed outside
  the checkout for both artifacts.
- Terraform format/init/validation, bootstrap and Fargate Terraform tests, Helm lint/render, Phase
  1B validation, and generated-project Terraform validation passed.
- Independent adversarial completion review passed with no material findings.
- Protected Linux CI still needs to repeat PostgreSQL integration, container, and security checks.

## Decisions

- All five modes reuse one bounded Parquet/S3 manifest/COPY path instead of adding a transport.
- Replace and SCD2 consume a complete logical stream; the other modes safely publish bounded batches.
- This expands implemented capability only; Redshift remains experimental and unqualified live.

## Remaining

- Push the focused PR, require protected CI, and merge only if clean.
- Keep direct transport, `SUPER`, graphs, views, telemetry, and paid live proof in separate slices.

## Review First

- `src/dander/providers/redshift/writer.py`
- `src/dander/providers/redshift/runtime.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
