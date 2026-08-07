# Morning Handoff

## Finished

- Added explicit exact-provider and portable model dialect metadata.
- Preserved BigQuery as the compatibility default and marked repository models explicitly.
- Added a closed portable SQL AST with deterministic ordering, casts, identifiers, and refs.
- Added BigQuery, Snowflake, Redshift, and PostgreSQL rendering for validated portable queries.
- Added positive and rejection fixtures plus exact-dialect mismatch coverage.

## Try It

Set `dialect: portable` in a model sidecar, use only `{{ ref('...') }}` relations, then call
`project.compile(model, target_dialect="postgres")`. Existing models need no changes.

## Checks

- All 887 tests, Ruff, formatting, and strict mypy across 199 source files passed.
- Wheel/sdist build, inspection, source-free installs, generated-project validation, and all four
  Terraform-root validations passed.
- Local container build and read-only runtime conformance passed as UID 65532.
- The isolated GCP plan reported `No changes`; both schedules stayed paused and no apply ran.

## Decisions

- Existing and undeclared models remain exact BigQuery SQL.
- Portable AST nodes are closed; new sqlglot nodes fail until explicitly reviewed.
- Render targets do not imply provider runtime support.

## Remaining

- Complete local and isolated GCP validation.
- Open the focused dialect PR and merge only after protected CI passes.
- Compile graph operations through the same provider-neutral boundary in the next PR.

## Review First

- `src/dander/transform/dialects.py`
- `src/dander/transform/project.py`
- `tests/transform/test_dialects.py`
