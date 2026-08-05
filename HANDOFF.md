# Morning Handoff

## Finished

- Merged the read-only source capability contract through protected PR #71.
- Merged `dander connector inspect` and `dander connector check` through protected PR #72.
- Prepared the `0.5.0rc1` package identity and release notes for protected CI.
- Kept normal ingestion, existing sources, Terraform state, and retained schedules unchanged.

## Try It

```bash
uv build
uv run python scripts/check_distribution.py dist/*.whl dist/*.tar.gz
```

## Checks

- PR #71 protected CI passed Python, 728 tests, dependency, Terraform, distribution, container,
  and secret checks.
- PR #72 protected CI passed Python, 733 tests, dependency, Terraform, distribution, container,
  and secret checks.
- Candidate Ruff and strict mypy passed; all 733 tests passed.
- Root and stage-zero Terraform validation passed.
- Wheel/sdist inspection and source-free outside-checkout installs from both artifacts passed.

## Decisions

- `0.5.0rc1` is the next minor candidate because `main` contains new commands and plugin/Druff
  capabilities after stable `0.4.0`.
- Publishing the immutable tag and PyPI package remains an explicit approval gate.
- Connector compatibility catalog ranges remain `>=0.4.0,<0.6`; they are not package-version pins.

## Remaining

- Merge the version-only candidate PR after protected CI passes.
- Obtain explicit approval before tagging or publishing `0.5.0rc1`.
- Raise and verify the Salesforce plugin dependency against the public candidate.
- Merge the prepared Salesforce capabilities through its own protected PR.
- Add safe operations through canonical graph transforms after the connector slice.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `.github/workflows/ci.yml`
