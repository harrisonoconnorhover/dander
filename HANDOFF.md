# Morning Handoff

## Finished

- Added one deterministic SCD1 fixture over the four warehouses' common scalar intersection.
- Added canonical readback normalization and exact expected/replay equality checks.
- Added sanitized per-provider evidence and exact four-provider comparison gates.
- Added owned cleanup verification and mandatory reviewed ceiling metadata.

## Try It

Run `.venv/bin/pytest -q tests/portability/test_warehouse_correctness.py`.

## Checks

- Credential-free conformance tests pass (6 tests).
- Full local pytest suite passes with expected opt-in skips.
- Ruff lint/format and focused strict mypy checks pass.
- Release metadata, wheel/sdist build, distribution inspection, and artifact inclusion pass.
- Protected CI passed on PR #182 at implementation commit `f1033f66`.

## Decisions

- Rows and credentials never enter evidence; only hashes, counts, and reviewed metadata do.
- Physical transport equality is excluded; provider-specific conformance remains separate.
- Live mutation requires a separately reviewed ceiling and stable approval reference.

## Remaining

- Obtain reviewed ceilings and run all four providers on one protected-main commit.
- Verify exact cleanup and retained GCP no-drift after those live runs.
- Merge sanitized equal-result evidence, then reassess the Phase 5 gate.

## Review First

- `scripts/benchmarks/warehouse_correctness.py`
- `tests/portability/test_warehouse_correctness.py`
- `docs/warehouse-correctness-conformance.md`
