# Morning Handoff

## Finished

- Classified RC28 Azure execution `dander-35e4e06fda09-sme7gpt` as a deterministic Snowflake
  portable-identifier defect after successful provider and canonical preflight.
- Cloned Snowflake portable ASTs and quoted every validated identifier before serialization.
- Added regression coverage for lowercase source columns, aliases, qualified joins, CTEs, and
  unchanged BigQuery, Redshift, and PostgreSQL output.
- Verified Snowflake transform-project compilation now preserves quoted lowercase columns.
- Protected the repository-wide container CVE repair independently in PR #361.

## Try It

Run `uv run pytest -q tests/transform/test_dialects.py
tests/providers/test_snowflake_warehouse_runtime.py`.

## Checks

- Focused transform and Snowflake provider tests passed.
- Ruff lint/format, canonical strict typing, and Control contract drift passed.
- Full pytest passed with 34 skips and one third-party deprecation warning.
- Container repair exact-main CI run `31986274883` passed all five jobs.

## Decisions

- Quote only a cloned AST for Snowflake so the same validated query remains reusable by other
  dialect renderers.
- Keep RC28 immutable and automatic retries disabled; no live provider work belongs in this PR.
- Preserve unaffected accepted evidence and rerun only Azure correctness on a protected replacement
  candidate.

## Remaining

- Complete protected review and merge this focused implementation.
- Pass exact-main CI before publishing the replacement immutable candidate.
- Run one bounded Azure correctness candidate and success-conditional replay under the remaining
  combined authorization, then clean up and reconcile cost.
- Complete the remaining Phase 8 provider, scale, soak, audit, and closure gates.

## Review First

- `src/dander/transform/dialects.py`
- `tests/transform/test_dialects.py`
- `tests/providers/test_snowflake_warehouse_runtime.py`
