# Morning Handoff

## Finished

- Made transform-project relation rendering target-dialect aware without changing BigQuery defaults.
- Added fenced PostgreSQL table, view, and incremental materializations.
- Added PostgreSQL-native not-null, unique, accepted-values, and relationship assertions.
- Wired PostgreSQL transform construction into the warehouse capability bundle.
- Added live PostgreSQL 15 replay, stale-owner, and sanitized-failure coverage.

## Try It

Set `DANDER_TEST_POSTGRES_DSN` to a disposable PostgreSQL 15+ database and run
`tests/providers/test_postgresql_transform_runtime.py`.

## Checks

- Twenty-nine focused PostgreSQL and transform tests passed locally.
- All 1,006 tests, repository-wide Ruff/formatting, and strict mypy passed locally.
- Wheel and sdist inspection passed and includes the PostgreSQL transform adapter.
- Protected CI remains to run before merge.

## Decisions

- PostgreSQL table replacement keeps a stable table and uses fenced truncate/insert in one transaction.
- Every model output has its own destination target-fence claim.
- PostgreSQL graphs and selectable hosted profiles remain separate slices.

## Remaining

- Run all repository checks and protected CI.
- Add version 2 PostgreSQL profile selection and runtime target-claim assembly.
- Add Kubernetes/Helm projection and an existing-cluster verification path.
- Run the PostgreSQL native and cross-backend matrices.

## Review First

- `src/dander/providers/postgresql/transform.py`
- `src/dander/transform/project.py`
- `tests/providers/test_postgresql_transform_runtime.py`
