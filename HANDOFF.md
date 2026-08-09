# Morning Handoff

## Finished

- Published the four-way BigQuery/PostgreSQL state/warehouse matrix through the installed CLI.
- Centralized fail-closed backend-pair selection without changing the existing executable pairs.
- Proved a BigQuery-issued authority/token fences PostgreSQL publication and rejects stale writes.
- Added a real PostgreSQL bounded-batch/concurrency/staging benchmark with honest qualification.
- Documented the matrix, measured local smoke, and remaining live qualification boundary.

## Try It

Run `dander runtime compatibility`. With a disposable PostgreSQL DSN, run
`uv run python -m scripts.benchmarks.postgresql --rows 100000 --payload-bytes 1024`.

## Checks

- All 1,031 tests passed with PostgreSQL 15; repository Ruff and strict mypy passed.
- A 50,000-row/52.4 MB logical-input smoke completed; stale publication was rejected and no
  temporary staging relation remained.
- Local smoke recorded 20,998 rows/second and explicitly remained `not_evaluated` for qualification.
- Wheel/sdist inspection and a clean wheel installation/CLI matrix read passed.

## Decisions

- Matrix status and executable behavior are separate from the supported capability manifest.
- Local throughput protects regressions; only an enforced-memory run may satisfy the scale SLO.
- PostgreSQL-state/BigQuery-warehouse remains unsupported and unchanged.

## Remaining

- Open and merge the focused protected PR if Linux package, container, Terraform, and scans pass.
- Qualify Kubernetes/PostgreSQL live before promoting that profile from experimental.
- Continue with Snowflake/Redshift only from the merged matrix baseline.

## Review First

- `src/dander/compatibility.py`
- `tests/portability/test_state_warehouse_matrix.py`
- `scripts/benchmarks/postgresql.py`
