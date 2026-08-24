# Morning Handoff

## Finished

- Prepared private candidate version `0.9.0rc32` from the protected Redshift Serverless correction.
- Added release notes limited to explicit Serverless credential acquisition.
- Kept the historical RC31 concurrency objective bound to its original immutable candidate.

## Try It

Run `uv run dander --version` and confirm `0.9.0rc32`.

## Checks

- Release metadata, full pytest, Ruff, strict typing, and Control contract drift pass.

## Decisions

- RC32 is private and only replaces RC31 for Redshift cells blocked by the shared connection boundary.
- Provisioned Redshift and unrelated qualification results remain unchanged.

## Remaining

- Protect and merge this candidate-preparation change.
- Publish one immutable multi-platform RC32 image from exact protected main.
- Bind only materially affected Redshift corrective objectives to that identity.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `tests/portability/test_redshift_concurrency_phase8_benchmark.py`
