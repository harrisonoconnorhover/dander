# Morning Handoff

## Finished

- Prepared the Dander `0.6.0` Beta release from the accepted `0.6.0rc2` runtime.
- Pinned the stable Salesforce `0.3.0` and ServiceNow `0.2.1` connector releases.
- Updated release metadata, public Beta labeling, acceptance records, and generated-project guidance.
- Kept runtime behavior unchanged; only curated catalog data and packaged templates changed under `src/dander`.

## Try It

Run `python3 scripts/check_release_metadata.py`, build with `uv build`, and install the artifacts in
a temporary environment outside this checkout.

## Checks

- Release metadata, Ruff lint/format, strict mypy, and all 776 tests passed.
- Root and stage-zero Terraform format/initialization/validation passed with backends disabled.
- Wheel and sdist built, passed distribution inspection, and installed source-free outside the checkout.
- Public Salesforce `0.3.0` and ServiceNow `0.2.1` installed together outside their repositories.

## Decisions

- Stable `0.6.0` preserves the accepted `rc2` runtime and moves Dander's public status to Beta.
- The curated catalog now advertises only the stable, Dander-`0.6.x` connector pins.

## Remaining

- Merge the protected release PR, tag and publish `0.6.0`, and create the GitHub Release.
- Deploy the public stable source-free image in the isolated project and repeat bounded smoke/no-drift checks.
- Upgrade the retained project only through its separately reviewed plan.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `src/dander/plugins/catalog.py`
