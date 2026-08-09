# Morning Handoff

## Finished

- Added experimental Snowflake table and incremental model execution.
- Preflights the full selected DAG before provider mutation and rejects views/graphs fail-closed.
- Publishes through session-temporary staging and exact transactional destination fencing.
- Preserves canonical database/schema/relation coordinates, including hyphenated databases.
- Updated Snowflake capability and limitation documentation without a support promotion.

## Try It

Configure the experimental profile in `docs/snowflake.md`; portable table and incremental models
may now run with active lease ownership. No live Snowflake qualification is claimed.

## Checks

- Full suite passed: 1,065 tests with PostgreSQL 15; Ruff and strict mypy passed.
- Wheel/sdist inspection, source-free installs, generated-project validation, and Terraform validation passed.
- Container build/conformance, Terraform/AWS tests, and dependency audit passed.
- No cloud plan/apply, deployment mutation, or package publication occurred.

## Decisions

- Snowflake view DDL remains excluded because it cannot share the destination-fence transaction.
- Transform targets permit create-if-absent with exact schema; automatic ALTER is not allowed.
- Incremental duplicates resolve by cursor descending, then stable declared-column ordering.

## Remaining

- Complete independent adversarial review.
- Push the stacked branch and open a draft PR against the Snowflake foundation branch.
- Let protected CI repeat Linux, packaging, container, security, and Terraform checks.
- Keep both Snowflake PRs unmerged while retained GCP baseline drift remains unresolved.

## Review First

- `src/dander/providers/snowflake/transform.py`
- `tests/providers/test_snowflake_warehouse_runtime.py`
- `src/dander/providers/snowflake/runtime.py`
