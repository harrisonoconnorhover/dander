# Morning Handoff

## Finished

- Extended the existing sanitized Redshift connection diagnostic to exercise Dander's IAM path first.
- Split connector startup from the exact read-only runtime-validation query on both credential paths.
- Bound at most 20 manual diagnostics to unchanged RC31, zero retries, and the remaining USD 0.35 ceiling.
- Preserved every prior Redshift attempt and all DANDER-204 terminal classifications.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_connection_diagnostic_phase8.py`.

## Checks

- Focused diagnostic tests, Ruff lint, Ruff formatting, strict typing, JSON parsing, and diff checks pass.
- No provider resource, benchmark cell, schema, COPY operation, or candidate changed.

## Decisions

- The Dander IAM path must run before explicit credential acquisition so the comparison cannot warm it first.
- The only allowed query is the existing read-only `current_database()` and `current_user` validation.
- Execution stops on a diagnostic distinction, an unexpected result, the run cap, or the cost cap.

## Remaining

- Protect the harness and objective, then require exact-main CI.
- Run only as many diagnostics as needed, up to 20 and within USD 0.35.
- Clean every owned AWS resource and record sanitized terminal evidence.
- Make a product correction only if the protected diagnostic proves one.

## Review First

- `scripts/benchmarks/redshift_connection_diagnostic_phase8.py`
- `tests/portability/test_redshift_connection_diagnostic_phase8.py`
- `docs/evidence/phase8/2026-08-23/aws-native-rc31-redshift-connection-reproduction-objective.json`
