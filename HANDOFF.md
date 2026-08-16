# Morning Handoff

## Finished

- Merged RC26's authenticated-but-stalled Redshift startup evidence as PR #332.
- Confirmed protected exact-main CI run `31923526315` passed all five jobs.
- Made Redshift Serverless request the driver's base text startup protocol.
- Kept provisioned Redshift on the official driver's default protocol.
- Added a focused connector-argument regression test and durable decision record.

## Try It

Run `uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py`.

## Checks

- The complete Redshift runtime suite passes: 56 tests.
- Focused Ruff lint and format checks pass.
- Focused strict typing passes for the changed runtime and test files.
- PR #332 and exact-main run `31923526315` passed protected CI.

## Decisions

- Request `client_protocol_version=0` only for Redshift Serverless.
- Treat this as a candidate correction, not live qualification evidence.
- Require a replacement immutable candidate and fresh protected AWS objective.

## Remaining

- Merge this focused startup correction through protected CI and review.
- Publish and verify the replacement candidate in a fresh branch.
- Create a fresh protected AWS objective bound to that candidate.
- Rerun the full AWS qualification objective without transferring RC26 results.
- Continue other Phase 8 lanes separately without colliding with DRUFF.

## Review First

- `src/dander/providers/redshift/runtime.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
- `docs/decisions.md`
