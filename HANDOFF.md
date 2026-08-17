# Morning Handoff

## Finished

- Protected the Snowflake identifier correction in PR #360 as main `a2b72f8`; exact-main passed.
- Merged the preserved RC28 failure evidence in PR #359 as protected main `dbd719b`.
- Prepared private `0.9.0rc29` package and lock metadata without changing public RC20.
- Limited RC29 notes to the Snowflake correction and protected container security refresh.
- Added DANDER-216 to bind source-free replacement publication to exact protected main.

## Try It

Run `uv run pytest -q tests/test_release_metadata.py && uv run python
scripts/check_release_metadata.py`.

## Checks

- Snowflake correction exact-main CI run `31987252875` passed all five jobs.
- Evidence PR #359 passed all five protected jobs before merge.
- RC29 release-metadata tests, metadata validation, and lock synchronization passed.
- RC29 wheel and source distribution built and passed package-content inspection.
- Ruff lint/format and canonical strict typing passed.

## Decisions

- Use RC29 as the immutable replacement; RC28 remains preserved and must not rerun.
- Reserve at most USD 0.25 from the additional authorization for one private source-free
  publication after protected gates pass.
- Preserve unaffected evidence and rerun only Azure correctness before the final closure matrix.

## Remaining

- Bind the authorization record to the rebased preparation commit and PR.
- Pass protected review, merge, and exact-main CI before any private publication.
- Publish and inspect one source-free amd64/arm64 candidate in a separate lane.
- Bind and run the replacement Azure correctness objective within the remaining combined cap.
- Complete the remaining Phase 8 provider, scale, pairwise, soak, audit, and closure gates.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `tickets/DANDER-216-private-rc29-candidate.md`
