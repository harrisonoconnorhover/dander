# Morning Handoff

## Finished

- Added bounded COPY-backed replace, SCD1, SCD2, snapshot, and incremental PostgreSQL writes.
- Kept replace/SCD2 endpoint-wide while preserving batching for the three batch-safe modes.
- Added cursor ordering, replay-safe snapshots, SCD2 history, and transactional fencing tests.
- Preserved full-row replace semantics and the pre-upgrade SCD1 unique-index identity.
- Updated the machine-readable capabilities and focused operator documentation.

## Try It

Set `DANDER_TEST_POSTGRES_DSN` to PostgreSQL 15+ and run
`uv run pytest -q tests/providers/test_postgresql_warehouse_runtime.py`.

## Checks

- Ruff passed across the repository; strict mypy passed across 304 source files.
- All 16 focused tests and the complete 1,189-test suite passed against PostgreSQL 15.
- Terraform roots/modules/tests and Helm lint/template validation passed.
- Wheel/sdist inspection, source-free install/generation, and dependency audit passed.
- Container conformance passed; retained GCP stage-zero and platform plans reported no changes.

## Decisions

- Replace and SCD2 stream a complete endpoint through COPY; they never use executor batches.
- Incremental de-duplication ranks cursor descending, then source ordinal for equal cursors.
- PostgreSQL remains experimental until graph and live Kubernetes qualification are complete.

## Remaining

- Let protected CI repeat Linux PostgreSQL, image, and secret checks on PR #175.
- Merge through protected main only when every required check is green.
- Continue PostgreSQL graph work separately; do not mix it into this writer PR.

## Review First

- `src/dander/providers/postgresql/writer.py`
- `tests/providers/test_postgresql_warehouse_runtime.py`
- `src/dander/providers/postgresql/runtime.py`
