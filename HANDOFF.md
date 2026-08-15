# Morning Handoff

## Finished

- Merged PR #324 as protected main `804496e`; exact-main CI run `31914082961` passed all five jobs.
- Merged the exact Redshift COPY staging-role correction in PR #325 as protected main `7cea5a8`.
- Prepared private `0.9.0rc26` metadata from that protected main as commit `2ab829f`.
- Opened draft PR #326 and bound private publication authorization to its preparation commit.
- Kept the aggregate Phase 8 ceiling at USD 10 by sharing the existing RC25 publication allocation.

## Try It

Run `uv run pytest -q tests/test_release_metadata.py && uv run python scripts/check_release_metadata.py`.

## Checks

- PR #325 and exact-main run `31914830354` passed all five protected checks.
- Four release-metadata tests, metadata/lock validation, Ruff, and diff checks pass.
- RC26 wheel and source distribution passed inspection; an outside-checkout wheel install generated
  a valid project with the exact staging-role grant and passed Terraform validation.
- Authorization JSON parses and planned allocations sum to exactly USD 10.

## Decisions

- Publish RC26 privately only after PR #326 and its exact-main CI pass.
- Share RC25's existing USD 0.25 publication allocation; do not increase the USD 10 ceiling.
- Keep public RC20, support status, and prior-candidate evidence unchanged.

## Remaining

- Complete protected CI/review and merge PR #326.
- Build and privately publish one source-free amd64/arm64 RC26 index.
- Commit a fresh candidate-bound AWS objective before provider mutation.
- Rerun manual execution, conditional replay, cost collection, and exact cleanup.
- Continue other Phase 8 lanes separately without colliding with DRUFF.

## Review First

- `docs/evidence/phase8/2026-08-15/rc26-authorization.json`
- `CHANGELOG.md`
- `docs/cloud-portability-phase8-qualification.md`
