# Morning Handoff

## Finished

- Added the lazy PostgreSQL warehouse runtime and explicit SCD1/COPY capability declaration.
- Implemented bounded `COPY` staging and deterministic last-record-wins upserts.
- Bound every PostgreSQL target mutation to the destination target-fence transaction.
- Added canonical PostgreSQL type mapping and narrow nullable-column evolution.
- Added PostgreSQL 15 live conformance coverage without enabling a hosted profile.

## Try It

Set `DANDER_TEST_POSTGRES_DSN` to a disposable PostgreSQL 15+ database, then run the two
PostgreSQL provider test modules. Tests create and remove isolated schemas.

## Checks

- Eleven PostgreSQL 15 state and warehouse tests passed against a local disposable container.
- All 1,004 tests, repository-wide Ruff/formatting, and strict mypy passed locally.
- Dependency audit found no known vulnerabilities; wheel and sdist inspection/install passed.
- GCP, AWS/Fargate, and cross-cloud Terraform formatting, tests, and validation passed.
- The runtime-all container built successfully and passed runtime conformance.

## Decisions

- Dander owns endpoint batching; PostgreSQL streams each supplied batch with `COPY`.
- Temporary staging is transaction-scoped with `ON COMMIT DROP`.
- Profile selection, transforms, assertions, and Kubernetes remain separate slices.

## Remaining

- Run protected CI and merge the focused pull request.
- Add version 2 PostgreSQL profile selection and bind state fences in runtime assembly.
- Implement PostgreSQL transforms and assertions against the portable SQL contract.
- Add the Kubernetes launcher after the warehouse composition is complete.

## Review First

- `src/dander/providers/postgresql/writer.py`
- `src/dander/providers/postgresql/runtime.py`
- `tests/providers/test_postgresql_warehouse_runtime.py`
