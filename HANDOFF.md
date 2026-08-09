# Morning Handoff

## Finished

- Added exact packaged capability reports for BigQuery, PostgreSQL, Redshift, and Snowflake.
- Added fail-closed canonical schema validation before extraction and destination mutation.
- Added field-path diagnostics for unsupported types, precision, and nested arrays.
- Kept the compatibility schema additive and the supported-profile manifest unchanged.
- Documented the implemented capability surface without promoting experimental providers.

## Try It

Run `dander runtime compatibility` and inspect the `warehouses` array. Portable-provider schemas
now fail through the selected provider's schema mapper before extraction or destination mutation.

## Checks

- Ruff and strict mypy across 304 source files passed; 1,110 tests passed with PostgreSQL 15.
- Wheel, sdist, source-free installs, runtime-all install, generated-project validation, dependency
  audit, and non-root/read-only container conformance passed.
- GCP/AWS Terraform validation/tests and Helm lint/template passed. The retained GCP read-only plan
  reported exactly `No changes.` with its reviewed 600-second override; no apply ran.

## Decisions

- Runtime capability declarations are authoritative; packaged JSON is checked against them.
- Unsupported semi-structured mappings remain fail-closed instead of silently selecting VARIANT
  or SUPER.
- This slice reports current behavior only; it adds no provider feature or support claim.

## Remaining

- Repeat the protected-quality-equivalent suite after the provider-neutral coordinate rebase.
- Reconcile the tracked 300-second job default and retained 600-second operator override in a
  separate change rather than mixing it into this capability slice.
- Continue provider write modes and live qualification separately; do not deploy or publish a
  package from this branch.

## Review First

- `src/dander/warehouse/runtime.py`
- `src/dander/compatibility.py`
- `tests/warehouse/test_schema_support.py`
