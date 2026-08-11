# Morning Handoff

## Finished

- Corrected Redshift direct-load binary serialization at the driver boundary.
- Projected Redshift `VARBYTE` as base64 text and strictly decoded it to bytes before normalization.
- Preserved the shared canonical schema, expected hashes, transport selection, and fencing behavior.
- Added credential-free direct-load, projection, decode, malformed-value, and wrong-type coverage.
- Corrected the documented CLI invocation so Snowflake's package cannot be shadowed by the script path.

## Try It

Run `.venv/bin/pytest -q tests/portability/test_warehouse_correctness.py`.

## Checks

- Warehouse-correctness contract tests pass (10 tests).
- Redshift runtime plus warehouse-correctness tests pass (64 tests).
- Focused Ruff lint/format and mypy pass.
- Protected-CI dependency profile passes Ruff, mypy, and full pytest (1,202 passed, 28 skipped).
- Bounded live Redshift validation passes the expected hash, replay, and owned cleanup.

## Decisions

- The Redshift driver sends byte parameters as hex text and decodes raw `VARBYTE` as UTF-8, so both
  conversions remain at its direct-load or live-qualification boundaries.
- Base64 is validated strictly and converted back to bytes so canonical normalization remains
  provider-neutral.
- No schema, transport selection, fencing, or non-binary behavior changes are included.

## Remaining

- Complete protected CI and merge the focused correction.
- Re-run all four providers on the resulting protected-main commit and compare equal evidence.
- Verify provider cleanup and retained GCP no-drift, then merge sanitized evidence.
- Reassess the revised Phase 5 gate without beginning Phase 6.

## Review First

- `scripts/benchmarks/warehouse_correctness.py`
- `tests/portability/test_warehouse_correctness.py`
- `docs/warehouse-correctness-conformance.md`
