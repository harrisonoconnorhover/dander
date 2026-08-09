# Morning Handoff

## Finished

- Added one bounded, compressed Parquet staging contract for Snowflake and Redshift loaders.
- Mapped canonical scalar, decimal, temporal, JSON, array, and record types explicitly to Arrow.
- Added per-part SHA-256, schema fingerprinting, row/byte counts, and deterministic manifest JSON.
- Added owner-only run directories and fail-closed cleanup limited to exact owned regular files.
- Made PyArrow an explicit Snowflake, Redshift, and full-runtime dependency.

## Try It

Use `ParquetStagingSession` as a context manager, call `stage(records, canonical_schema)`, upload the
returned immutable parts inside the context, and let normal exit remove local artifacts.

## Checks

- All 1,036 tests passed with PostgreSQL 15; repository Ruff and strict mypy passed.
- Staging round-trip, row/byte splitting, redaction, cleanup, and dependency tests passed.
- Wheel/sdist inspection and Snowflake/Redshift/runtime-all PyArrow metadata checks passed.

## Decisions

- Shared code owns local artifacts only; provider adapters own remote stages and publication.
- JSON uses deterministic compact text; provider-native semi-structured mapping remains explicit.
- A singleton oversized row may exceed the soft byte target so staging always makes progress.

## Remaining

- Open and merge the focused protected PR if Linux package, container, Terraform, and scans pass.
- Build Snowflake upload/COPY/fencing against this contract in a separate branch.
- Build Redshift S3/COPY/fencing only after the shared contract merges.

## Review First

- `src/dander/warehouse/staging.py`
- `tests/warehouse/test_staging.py`
- `docs/warehouse-staging.md`
