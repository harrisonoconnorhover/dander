# Morning Handoff

## Finished

- Promoted current-public install, status, upgrade, audit, session, and Phase 1B references from
  `0.9.0rc18` to the explicitly approved `0.9.0rc19` candidate.
- Updated release-metadata tests so publication mode proves prepared and public RC19 match.
- Preserved every historical RC18 contract-release claim and provider-support limitation.
- Left the reviewed RC19 package contents, version, lockfile, changelog, and workflow unchanged.

## Try It

Run `uv run python scripts/check_release_metadata.py --publication`; it now passes for RC19.

## Checks

- Normal and publication-mode release metadata validation passed for RC19.
- Focused release tests and Ruff lint/format passed.
- Control-contract drift and RC19 wheel/source-distribution validation passed.
- Git whitespace and secret/artifact diff review passed.
- No tag, PyPI artifact, provider resource, or paid action was created by this PR.

## Decisions

- Promotion changes only current-public references; exact historical artifact claims remain fixed.
- Tagging waits for protected PR and exact-main CI on the promotion commit.
- Druff consumption waits for immutable PyPI verification and the post-public evidence PR.

## Remaining

- Complete the independent promotion review and protected PR/exact-main CI.
- Create immutable `v0.9.0rc19` and complete the approval-gated PyPI workflow.
- Verify public RC19 outside the checkout and create the matching GitHub prerelease.
- Record immutable release evidence before Druff consumes RC19.

## Review First

- `scripts/check_release_metadata.py`
- `tests/test_release_metadata.py`
- `.github/workflows/publish.yml`
