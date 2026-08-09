# Morning Handoff

## Finished

- Made PostgreSQL warehouse/state selectable together through a version 2 deployment.
- Added named deployment selection to `dander run` and the OCI runtime.
- Claimed required ingestion targets before extraction without changing BigQuery writer behavior.
- Made metadata compilation use the selected warehouse dialect.
- Added one complete PostgreSQL 15 ingestion, transform, metadata, replay, cursor, history, and lease proof.

## Try It

Set `DANDER_TEST_POSTGRES_DSN` to a disposable PostgreSQL 15+ database and run
`tests/integration/test_postgresql_native_profile.py`.

## Checks

- All 1,010 repository tests passed locally, including the real PostgreSQL 15 integration.
- Repository-wide Ruff and strict mypy passed locally.
- Wheel/sdist inspection, source-free installation, and dependency audit passed.
- Terraform validation/tests and the full-runtime container build/conformance probe passed.

## Decisions

- Writers explicitly declare when the neutral runner must claim a destination target fence.
- A native PostgreSQL/no-catalog/environment-secret profile needs no GCP project identifier.
- PostgreSQL-state/BigQuery-warehouse remains fail-closed until all BigQuery writers adopt target fencing.

## Remaining

- Open, review, and merge the focused protected PR.
- Let protected CI repeat Linux container/security/secret checks.
- Add Kubernetes/Helm projection and existing-cluster verification separately.
- Run the PostgreSQL native and cross-backend benchmark matrix.

## Review First

- `src/dander/cli/run_command.py`
- `src/dander/runtime.py`
- `tests/integration/test_postgresql_native_profile.py`
