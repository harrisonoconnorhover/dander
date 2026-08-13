# Morning Handoff

## Finished

- Promoted current-public install, status, upgrade, audit, session, and Phase 1B references from
  `0.9.0rc17` to the explicitly approved `0.9.0rc18` candidate.
- Updated release-metadata tests so publication mode proves prepared and public RC18 match.
- Preserved every historical RC17 Phase 7 acceptance, limitation, decision, and evidence record.
- Left the already-reviewed RC18 package contents, version, lockfile, changelog, and workflow
  unchanged.

## Try It

Run `uv run python scripts/check_release_metadata.py --publication`; it now passes for RC18.

## Checks

- Ruff lint and format passed across 398 files.
- Mypy passed across 368 source files after syncing the exact CI `dev` and `postgres` extras.
- Control-contract drift validation passed for `io.dander.control.contracts/v1`.
- Full tests passed: 1,424 passed and 28 skipped.
- Normal/publication metadata checks and wheel/source-distribution validation passed.

## Decisions

- Promotion changes only current-public references; exact historical artifact claims remain fixed.
- The promotion commit may merge only after independent review and protected CI pass.
- Druff consumption still waits for the immutable RC18 tag, successful PyPI workflow, and clean
  outside-checkout install verification.

## Remaining

- Complete the independent promotion review.
- Open, pass, and merge the focused protected promotion PR.
- Create `v0.9.0rc18` and complete the approval-gated PyPI workflow.
- Install and verify public RC18 in a fresh environment outside the checkout.
- Generate the Druff consumer from the verified public artifact.

## Review First

- `scripts/check_release_metadata.py`
- `tests/test_release_metadata.py`
- `.github/workflows/publish.yml`
