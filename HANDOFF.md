# Morning Handoff

## Finished

- Prepared stable Dander `0.7.0` from the accepted `0.7.0rc2` lineage.
- Published stable Salesforce `0.3.1` and ServiceNow `0.2.2` connector packages.
- Updated stable package, catalog, example, installation, upgrade, release, and handoff references.
- Kept Dander runtime implementation unchanged from the accepted candidate.

## Try It

Install the built package outside the checkout, generate a project, and verify that exact stable
connector pins install without changing Dander `0.7.0`.

## Checks

- Phase 1 source-free local/Cloud Run acceptance and final Terraform no-drift passed.
- All 815 tests, Ruff lint/format, strict mypy, package build, and external stable resolution passed.

## Decisions

- Promote the accepted candidate without a functional runtime change.
- Phase 1B remains a separate AWS artifact-copy and keyless-identity gate.

## Remaining

- Merge through protected main, tag `v0.7.0`, and publish through the protected environment.
- Verify public stable installation and create the GitHub Release.
- Begin Phase 1B in a separate branch without touching the retained project.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `src/dander/plugins/catalog.py`
