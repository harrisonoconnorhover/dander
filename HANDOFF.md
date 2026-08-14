# Morning Handoff

## Finished

- Promoted current-public install, status, upgrade, audit, session, and Phase 1B references from
  `0.9.0rc19` to the authorized `0.9.0rc20` candidate.
- Updated release-metadata tests so publication mode proves prepared and public RC20 match.
- Preserved historical RC19/RC18 artifact evidence and every provider-support limitation.
- Left package contents, version, lockfile, changelog, and publishing workflow unchanged.

## Try It

Run `uv run python scripts/check_release_metadata.py --publication`; it now passes for RC20.

## Checks

- Normal and publication-mode release metadata validation pass for RC20.
- Focused release tests and Ruff lint/format pass.
- Control-contract drift and RC20 wheel/source-distribution validation pass.
- Git whitespace and secret/artifact diff review pass.

## Decisions

- Promotion changes only current-public references; exact historical artifact claims stay fixed.
- Tagging waits for protected PR and exact-main CI on the promotion merge commit.
- Candidate publication does not qualify the local profile or promote provider support.

## Remaining

- Complete independent completion review and protected PR/exact-main CI.
- Create immutable `v0.9.0rc20`, publish the GitHub prerelease and PyPI package, and verify them.
- Record immutable RC20 evidence before using its candidate image for local qualification.

## Review First

- `scripts/check_release_metadata.py`
- `tests/test_release_metadata.py`
- `.github/workflows/publish.yml`
