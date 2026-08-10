# Morning Handoff

## Finished

- Guaranteed a current Snowflake schema before connector-managed direct qmark binding.
- Rejected JSON-to-`VARIANT` fields as keys, incremental cursors, or snapshot identity.
- Added one opt-in disposable-schema Snowflake warehouse qualification harness.
- Covered direct/COPY threshold crossing, all writer modes, replay, cursor safety, fencing, graph execution, readback, and cleanup.
- Documented the paid-test boundary and kept Snowflake explicitly experimental.

## Try It

Run `uv run pytest -q tests/providers/test_snowflake_warehouse_runtime.py
tests/portability/test_snowflake_qualification.py`. The live command is in `docs/snowflake.md` and
requires a separately approved Snowflake test account and credit ceiling.

## Checks

- Ruff, formatting, strict mypy, and all 1,205 tests passed; PostgreSQL integration used PostgreSQL 15.
- Dependency audit, wheel/sdist inspection, source-free installs, and runtime-all import passed.
- Non-root read-only container conformance and packaged proof-asset checks passed.
- Terraform roots/tests and Helm lint/template validation passed.
- Fresh retained GCP stage-zero and platform plans each reported exactly `No changes.`

## Decisions

- Use existing provider/runtime contracts rather than creating a benchmark framework.
- Mutate and remove one random `DANDER_QUAL_*` schema; never provision account-level resources.
- Keep direct thresholds at zero by default until real-account timing evidence exists.

## Remaining

- Let protected CI repeat Linux packaging, container, security, Terraform, and Helm checks.
- Configure non-interactive access for the signed-in Snowflake trial account.
- Approve a trial-credit ceiling, run the live harness, and preserve its sanitized report.
- Compare normalized results with the shared cross-warehouse conformance fixture.

## Review First

- `src/dander/providers/snowflake/writer.py`
- `scripts/benchmarks/snowflake.py`
- `tests/portability/test_snowflake_qualification.py`
