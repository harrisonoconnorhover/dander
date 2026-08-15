# Morning Handoff

## Finished

- Merged RC24 failure evidence as protected-main commit `41147c6`.
- Confirmed all five exact-main CI jobs passed in run `31901767206`.
- Prepared private `0.9.0rc25` metadata from that exact protected main.
- Opened draft PR #317 and bound publication authorization to preparation commit `d974a97`.
- Allocated the final USD 0.25 contingency without changing the USD 10 aggregate ceiling.

## Try It

Run `uv run pytest -q tests/test_release_metadata.py && uv run python scripts/check_release_metadata.py`.

## Checks

- Exact protected-main run `31901767206` passed all five jobs at `41147c6`.
- Four release-metadata tests, metadata/lock validation, Ruff, and diff checks passed.
- RC25 wheel and source distribution built and passed distribution inspection.
- Authorization JSON parses and planned allocations sum to exactly USD 10.

## Decisions

- Publish RC25 privately only after PR #317 and its exact-main CI pass.
- Keep public RC20, support status, and prior-candidate evidence unchanged.
- No unallocated contingency remains; actual provider spend must stay below USD 10.

## Remaining

- Complete protected CI/review and merge PR #317.
- Build, privately publish, and inspect one source-free amd64/arm64 RC25 index.
- Resume AWS-native manual correctness and replay in a fresh objective lane.
- Record AWS cost only after billing data posts.

## Review First

- `docs/evidence/phase8/2026-08-15/rc25-authorization.json`
- `CHANGELOG.md`
- `pyproject.toml`
