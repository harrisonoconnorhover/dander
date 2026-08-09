# Morning Handoff

## Finished

- Added experimental Redshift portable/provider-exact table and incremental model execution.
- Preflighted the complete selected DAG, schemas, SQL, materializations, and assertions before any
  provider connection, claim, or mutation.
- Published through session-temporary CTAS plus fenced table replacement or cursor-monotonic
  `UPDATE`/`INSERT`; target DML and the exact fence touch share one transaction.
- Added generic assertions, stale-fence/cleanup/replay coverage, and a packaged Redshift guide.
- Kept views, graphs, schema evolution, `SUPER`, other write modes, and support promotion blocked.

## Try It

Configure a v2 `warehouse.provider: redshift` profile as shown in `docs/redshift.md`, author a
portable or Redshift-exact table/incremental model, and use the normal hosted run path. This slice
was conformance-tested locally; no AWS warehouse was created or contacted.

## Checks

- Ruff and strict mypy passed; focused Redshift/transform tests passed.
- Full suite passed with 1,086 tests against an ephemeral PostgreSQL 15 service.
- Wheel/sdist inspection, outside-checkout generation/install, full-runtime install, dependency
  audit, Terraform/AWS/Helm validation, and generated-project Terraform validation passed.
- The final source-free container passed non-root, read-only, runtime, and bundled-asset checks.

## Decisions

- Redshift incremental publication uses documented `UPDATE ... FROM` plus `INSERT ... WHERE NOT
  EXISTS`; Redshift does not support Snowflake-style conditional `WHEN MATCHED` clauses.
- Canonical database/schema/relation coordinates survive compilation; Redshift renders local
  schema/table target DML only at its provider boundary.
- Existing destination fencing and strict transform schemas are reused without a parallel runtime.

## Remaining

- Run the retained GCP no-drift plan; never apply it.
- Push the focused branch, let protected CI validate it, and merge only if every check is green.
- Continue the cloud-portability roadmap with a separate next slice.

## Review First

- `src/dander/providers/redshift/transform.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
- `docs/redshift.md`
