# Morning Handoff

## Finished

- Preserved C7 as the unexpected exact-RC32 runtime-construction timeout with no workload mutation.
- Completed exact C7 cleanup: 37 resources, 12 state versions, and exact lock metadata removed.
- Rebound the existing read-only connection diagnostic to RC32 and Dander's current factory.
- Put explicit temporary credentials first while preserving TLS, protocol, and timeout settings.
- Added one bounded C8 objective for up to 20 manual, zero-retry diagnostics.

## Try It

Run `uv run --isolated --frozen --extra dev --extra postgres pytest tests/portability/test_redshift_connection_diagnostic_phase8.py -q`.

## Checks

- Focused diagnostic suite passes with 7 tests; full suite passes with 1,947 tests and 35 skips.
- Ruff lint/format, strict types, contracts, dependency audit, Terraform format, JSON, and whitespace pass.

## Decisions

- Diagnostic output remains limited to stage name, elapsed time, and exception class.
- C8 permits no schema, COPY, benchmark, or candidate mutation.
- The USD 4.00 diagnostic ceiling draws only from the existing reserved allocation.

## Remaining

- Protect and merge the C8 diagnostic objective.
- Verify exact-main CI before paid mutation.
- Run only until the shared connection boundary is solidly classified, then clean every owned resource.
- Select a product correction only from the protected diagnostic result.

## Review First

- `scripts/benchmarks/redshift_connection_diagnostic_phase8.py`
- `tests/portability/test_redshift_connection_diagnostic_phase8.py`
- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-connection-diagnostic-objective.json`
