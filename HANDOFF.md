# Morning Handoff

## Finished

- Prepared `dander-platform==0.9.0rc18` for the merged Dander Control contract bundle.
- Separated the prepared package version from the actually public version in release validation.
- Kept install, status, upgrade, audit, and Phase 1B references honestly on public RC17.
- Made the protected publication workflow reject an unpromoted prepared version.
- Documented the two-stage preparation and publication gate.

## Try It

Run `uv run python scripts/check_release_metadata.py`; publication mode intentionally fails until
an explicitly approved promotion changes current-public references to RC18.

## Checks

- Repository Ruff lint/format and mypy passed across 368 source files.
- Full test suite passed: 1,424 tests, with 28 skipped.
- Normal release metadata validation passed for prepared RC18/public RC17.
- Publication-mode validation failed closed on RC18 versus public RC17, as required.
- Built RC18 wheel/source distribution and recursive Terraform formatting checks passed.

## Decisions

- A prepared version is not described as public before its package exists.
- Publication requires a separate approved promotion commit plus exact tag and environment review.
- Historical Phase 7 evidence remains fixed on the exact RC17 artifact it proved.

## Remaining

- Complete the final adversarial review and protected PR/CI review.
- Obtain explicit approval before promoting, tagging, or publishing RC18.
- Generate the Druff consumer only from the approved immutable RC18 artifact.

## Review First

- `scripts/check_release_metadata.py`
- `.github/workflows/publish.yml`
- `docs/releasing.md`
