# Morning Handoff

## Finished

- Prepared replacement Dander candidate `0.7.0rc2` after rejecting rc1 before deployment.
- Prevented connector installation from silently downgrading the running Dander package.
- Published compatible Salesforce `0.3.1rc1` and ServiceNow `0.2.2rc1` candidates.
- Updated the curated catalog, Salesforce example, and public version references.

## Try It

Tag the merged release commit `v0.7.0rc2`, publish through the protected workflow, and install it
outside the checkout before any isolated cloud apply.

## Checks

- Release metadata, all 815 tests, Ruff lint/format, strict mypy, and package build passed.
- Connector candidate workflows and clean external resolution with `0.7.0rc1` passed.

## Decisions

- `0.7.0rc1` is immutable but rejected because its source-free plugin install downgraded Dander.
- `0.7.0rc2` replaces rc1 without expanding the Phase 1 portability scope.

## Remaining

- Merge the release PR through protected main and publish the immutable candidate.
- Run source-free local/isolated Cloud Run parity, replay, signals, cleanup, and no-drift proof.
- Do not touch the retained project during candidate acceptance.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `src/dander/cli/main.py`
