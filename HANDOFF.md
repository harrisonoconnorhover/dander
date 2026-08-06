# Morning Handoff

## Finished

- Added a release-metadata check covering package metadata, public docs, changelog, and templates.
- Made CI and artifact validation derive the version from `pyproject.toml`.
- Made wheel and sdist validation reject stale PyPI descriptions.
- Synchronized public candidate documentation on `0.6.0rc1` while retaining `0.5.1` as stable alpha.
- Added a regression test for the prior `0.1.0` README drift.

## Try It

Run `python3 scripts/check_release_metadata.py`. Build with `uv build`, then pass the wheel and
sdist to `python3 scripts/check_distribution.py`.

## Checks

- Ruff lint and format checks passed across the repository.
- Strict mypy passed across `src` and `tests`.
- Full test suite passed: `766 passed`.
- Wheel and sdist built and passed identity, contents, hygiene, and description validation.
- `git diff --check` passed.

## Decisions

- PyPI release descriptions are immutable, so the live correction must arrive in a new release.
- `pyproject.toml` is the single version source; publication-facing copy must match it exactly.
- `0.6.0rc1` is the prepared candidate; `0.5.1` remains the latest stable alpha.

## Remaining

- Review and merge the release-metadata pull request after protected CI passes.
- Publish `0.6.0rc1` only with explicit approval for the full candidate, not merely its metadata.
- Verify the live PyPI page after publication.

## Review First

- `scripts/check_release_metadata.py`
- `scripts/check_distribution.py`
- `.github/workflows/publish.yml`
