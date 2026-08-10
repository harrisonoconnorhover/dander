# Morning Handoff

## Finished

- Added opt-in bounded Redshift direct staging with paired row/byte thresholds.
- Preserved zero-default COPY behavior and whole-endpoint fallback without losing the inspected prefix.
- Reused all five fenced write modes, replay identity, schema checks, SUPER parsing, and cleanup.
- Added unattributed direct telemetry from exact local counters; direct loads never contact S3.
- Updated Redshift configuration, compatibility output, decisions, and operator documentation.

## Try It

Set both `direct_max_rows` and `direct_max_logical_bytes` in a Redshift warehouse profile, then run
`uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py`.

## Checks

- Ruff passed across 328 files; strict mypy passed across 304 files.
- Full pytest passed: 1,165 tests with 13 intentional skips.
- GCP/AWS Terraform roots validated; AWS bootstrap and Fargate module tests passed without apply.
- Wheel/sdist inspection, source-free installs, runtime-all import, and generated Terraform validation passed.
- Dependency audit found no known vulnerabilities; container, secret, and PostgreSQL-service checks await protected CI.

## Decisions

- COPY remains default; both direct thresholds must be positive to enable endpoint-wide selection.
- Threshold overflow falls back once to COPY; executor batching is disabled only while direct selection is enabled.
- Direct `executemany` telemetry has no query ID because Redshift exposes no honest whole-batch ID.

## Remaining

- Push a focused PR and require protected CI before merge.
- Run live Redshift/Glue acceptance only after a separately approved paid AWS plan/apply.
- Complete Snowflake and cross-warehouse live proof before Phase 5.5; do not begin Azure.

## Review First

- `src/dander/providers/redshift/writer.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
- `docs/redshift.md`
