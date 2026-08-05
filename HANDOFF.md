# Morning Handoff

## Finished

- Published public Dander `0.4.0rc3` and deployed its source-free image with Salesforce plugin `0.1.0rc1` into disposable project `project-092b24a8-26a3-4438-8cd`.
- Applied reviewed stage-zero and platform plans with the scheduler paused; both now return exactly `No changes.`
- Proved dynamic Salesforce discovery, canonical graph save/validation, Druff bridge execution, replay, and controlled-overlap behavior.
- Verified 14 unique Salesforce rows, a monotonic cursor, one skipped overlapping run, a released lease, and no run-scoped staging residue.
- Published and source-free-verified `0.4.0rc4`, passed its live verifier, and prepared the version-only `0.4.0` final release.

## Try It

```bash
uv run pytest tests/bootstrap/test_verifier_contracts.py tests/bootstrap/test_verify.py -q
uv run dander --version
uv build
uv run python scripts/check_distribution.py dist/*.whl dist/*.tar.gz
```

## Checks

- Ruff lint/format, strict mypy, and all 693 tests passed.
- Terraform format and backend-disabled initialization/validation passed for platform and stage zero.
- The corrected verifier passed every check against the live unguarded deployment.
- Initial run, bridge-triggered replay, and both overlap executions completed; Dander recorded one overlap as skipped.
- Final stage-zero and platform Terraform plans both reported exactly `No changes.`

## Decisions

- Publish Dander `0.4.0` before the Salesforce plugin final raises its dependency floor to stable Dander.
- Keep final runtime and Terraform source unchanged from accepted `0.4.0rc4`.
- Keep connector discovery and execution evidence API-based; no browser UI click was claimed.

## Remaining

- Merge the version-only `0.4.0` final release PR after protected CI.
- Obtain explicit approval before tagging or publishing the merged `0.4.0` commit.
- Prepare the Salesforce plugin `0.1.0` final against public Dander `>=0.4.0,<0.5`.
- Verify the two final public packages together in one source-free build before ServiceNow extraction.
- Keep the isolated scheduler paused and the retained proof project untouched until a separately reviewed apply.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `.github/workflows/ci.yml`
