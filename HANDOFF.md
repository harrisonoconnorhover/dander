# Morning Handoff

## Finished

- Merged the focused Redshift Serverless startup correction in PR #333 as protected main `141fab6`.
- Confirmed exact-main CI run `31924339366` passed all five jobs.
- Prepared private `0.9.0rc27` metadata as exact commit `30abed9` from that protected main.
- Opened draft PR #334 and bound private publication to its exact preparation commit.
- Kept the aggregate Phase 8 ceiling at USD 10 by sharing the existing publication allocation.

## Try It

Run `uv run pytest -q tests/test_release_metadata.py && uv run python scripts/check_release_metadata.py`.

## Checks

- Four release-metadata tests, lock/metadata validation, Ruff, and diff checks pass.
- RC27 wheel and source distribution passed package inspection.
- Both artifacts installed outside the checkout, generated valid projects, and passed validation.
- The full runtime dependency import and generated-project Terraform validation pass.

## Decisions

- Publish RC27 privately only after PR #334 and its exact-main CI pass.
- Share the existing USD 0.25 RC25/RC26/RC27 publication allocation; do not raise the ceiling.
- Keep public RC20, support status, retained jobs, and all prior-candidate results unchanged.

## Remaining

- Complete protected CI/review and merge PR #334.
- Build, inspect, and privately publish one source-free amd64/arm64 RC27 index in a fresh lane.
- Commit a fresh protected AWS objective bound to the published RC27 digest.
- Rerun the complete AWS objective without transferring RC26 results.
- Continue other Phase 8 lanes separately without colliding with DRUFF.

## Review First

- `docs/evidence/phase8/2026-08-15/rc27-authorization.json`
- `CHANGELOG.md`
- `docs/cloud-portability-phase8-qualification.md`
