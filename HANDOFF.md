# Morning Handoff

## Finished

- Prepared metadata-only Dander `0.8.0rc1` from current protected main.
- Recorded the rejected public-`0.7.0` Phase 1B image and complete teardown.
- Updated release-facing version references, lockfile, and generated-project assertions.
- Corrected the retained runtime record to Dander `0.7.1` with the stable connector pins.

## Try It

After explicit publication approval, tag the merged commit `v0.8.0rc1`, publish through the
protected workflow, and install it outside the checkout before rebuilding Phase 1B.

## Checks

- Ruff, format, strict mypy, dependency audit, and 1,113 tests passed against PostgreSQL 15.
- Wheel/sdist inspection, isolated source-free and runtime-all installs, generated-project
  validation, Terraform, Helm, and non-root/read-only container conformance passed.
- Retained stage zero and the unchanged `main` runtime/infra planner each reported exactly
  `No changes.` The candidate planner proposed only five expected `dander_version` label updates;
  no plan was applied.

## Decisions

- Use the next minor candidate because public `0.7.0` is immutable and current main adds features.
- Keep this PR metadata-only; the accepted candidate must contain the already-reviewed main code.
- Treat the candidate's planner-version labels as release metadata, not live infrastructure drift.

## Remaining

- Let protected CI repeat Linux packaging, container, configuration, and secret scans.
- Merge through protected main, then obtain explicit tag/PyPI publication approval.
- Rebuild Phase 1B once from the public candidate using a fresh proof identity.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `acceptance/cloud-portability/phase1b/README.md`
