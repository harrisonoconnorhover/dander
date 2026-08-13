# Morning Handoff

## Finished

- Diagnosed the RC14 OCI run: ingestion and cleanup passed; the staging transform failed on BigQuery-only nested-field syntax.
- Added explicit `<model>.<provider>.sql` selection with shared metadata and orphan-variant rejection.
- Added a PostgreSQL Greenhouse model variant that preserves `location_name` through JSONB extraction.
- Kept the base Greenhouse model exact BigQuery SQL instead of weakening the portable SQL contract.

## Try It

Run `uv run pytest -q tests/transform/test_project.py tests/project/test_scaffold.py tests/providers/test_postgresql_transform_runtime.py`.

## Checks

- Ruff format/check passed for changed Python files.
- Focused transform, scaffold, CLI, and PostgreSQL suites passed; one existing integration test skipped.
- A live local PostgreSQL run ingested 17 public rows and completed the corrected transform/assertions.

## Decisions

- Use an explicit PostgreSQL model variant for JSONB path semantics; do not call provider JSON paths portable.
- Keep variants attached to one base model and metadata spine, and fail closed when the base is absent.

## Remaining

- Commit, open the focused PR, and require protected CI.
- Publish/promote the corrected candidate after merge.
- Resume OCI success, replay, overlap, cancel, retry, schedule, rotation, rollback, cleanup, and no-drift proofs.
- Complete Phase 7 evidence and Phase 8 qualification.

## Review First

- `src/dander/transform/project.py`
- `models/staging/stg_greenhouse__jobs.postgres.sql`
- `tests/transform/test_project.py`
