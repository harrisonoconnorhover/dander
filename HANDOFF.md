# Morning Handoff

## Finished

- Prepared Dander `0.7.0rc1` release metadata from merged Phase 1 code.
- Added candidate notes for the OCI runtime, artifact, execution-projection, and Cloud Run work.
- Updated the lockfile and exact public install/support references.
- Kept `src/dander` functional runtime code unchanged from the reviewed Phase 1 merge.

## Try It

After explicit publication approval, tag this exact merged commit `v0.7.0rc1`, publish through the
protected workflow, and install it outside the checkout before any isolated cloud apply.

## Checks

- Release metadata consistency passed.
- Ruff format/lint and strict mypy passed.
- All 815 tests passed.
- Wheel/sdist build and distribution inspection passed.
- `src/dander` is byte-unchanged from the accepted Phase 1 merge.

## Decisions

- `0.7.0rc1` is the Phase 1 portability candidate; it makes no support claim beyond GCP.
- Candidate publication and isolated Cloud Run mutation remain separate explicit approval gates.

## Remaining

- Merge the version-only PR through protected main.
- Obtain explicit tag/PyPI approval and publish the immutable candidate.
- Run source-free local/isolated Cloud Run parity, replay, signals, cleanup, and no-drift proof.
- Do not touch the retained project during candidate acceptance.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `docs/known-limitations.md`
