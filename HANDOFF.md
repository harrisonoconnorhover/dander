# Morning Handoff

## Finished

- Prepared Dander `0.9.0rc1` from protected main without changing runtime behavior.
- Reconciled every exact public-version reference required by the release gate.
- Moved the accumulated Phase 5 and Phase 6 notes into the `0.9.0rc1` changelog entry.

## Try It

Run `python3 scripts/check_release_metadata.py`, then build and inspect the distribution.

## Checks

- Release metadata validation passed for `dander-platform 0.9.0rc1`.
- Wheel and source distribution validation passed.
- Ruff, format, mypy, and the full pytest suite passed locally.
- `git diff --check` passed.
- Full protected CI remains required before merge and again on protected main before tagging.

## Decisions

- Publish only from an exact `v0.9.0rc1` tag on the green protected-main merge commit.
- Keep Azure experimental; the final Phase 6 public-candidate proof/evidence merge remains pending,
  and Phase 8 remains the support gate.

## Remaining

- Merge this version-only PR through protected CI.
- Tag and publish `0.9.0rc1` through the reviewed `pypi` environment.
- Install `0.9.0rc1` outside the checkout and run the required public-artifact smoke proofs.

## Review First

- `pyproject.toml`
- `CHANGELOG.md`
- `scripts/check_release_metadata.py`
