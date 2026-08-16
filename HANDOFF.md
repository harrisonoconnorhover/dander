# Morning Handoff

## Finished

- Merged Azure runtime-platform projection as PR #350 at protected main `1436092`.
- Confirmed exact-main CI run `31960158477` passed all five jobs.
- Prepared private `0.9.0rc28` metadata from that protected main.
- Limited RC28 release notes to the stable qualification entrypoint and Azure platform handoff.
- Opened draft PR #351 and bound publication to exact preparation commit `130a970`.

## Try It

Run `uv run pytest -q tests/test_release_metadata.py && uv run python scripts/check_release_metadata.py`.

## Checks

- Exact-main run `31960158477` passed all five protected jobs.
- Four release-metadata tests and metadata validation passed.
- RC28 wheel and source distribution passed package inspection.
- Ruff, canonical strict typing, contract drift, JSON, and diff checks passed; the lock is
  synchronized.

## Decisions

- Publish only after preparation merges and its exact-main CI passes, within the existing
  cumulative publication allocation and unchanged USD 10 ceiling.
- Preserve accepted RC27 evidence; rerun materially affected lanes plus the final closure matrix.
- Keep public RC20, support status, and all live-provider gates unchanged.

## Remaining

- Pass protected CI/review and merge the preparation.
- Build, inspect, and privately publish source-free amd64/arm64 RC28 in a separate lane.
- Bind Azure qualification in another fresh protected-main branch before mutation.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `tickets/DANDER-210-private-rc28-candidate.md`
