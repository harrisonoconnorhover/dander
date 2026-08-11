# Morning Handoff

## Finished

- Added explicit Azure BigQuery `--gcp-project` planning input for version-2 profiles.
- Added focused CLI coverage for the missing-input failure and successful projection.
- Documented why the GCP project remains deployment scope rather than platform configuration.

## Try It

Run `uv run pytest tests/cli/test_init_cli.py -k azure_bigquery`.

## Checks

- `uv run pytest tests/cli/test_init_cli.py -q` passed (16 tests).
- Related Azure Terraform, portable-config, and operations suites passed (35 tests).
- Ruff check/format and mypy passed for the changed Python files.
- Protected CI remains to run.

## Decisions

- Keep the concrete GCP project explicit at Azure plan time; do not add it to the reusable profile.
- Keep the accepted runtime digest unchanged because this correction affects only operator planning.

## Remaining

- Merge this focused correction through protected CI.
- Resume the approved Azure-to-Google live federation plan and refresh proof.
- Clean up disposable Azure, Snowflake, and federation resources.
- Merge sanitized Phase 6 evidence and verify retained-GCP no drift.

## Review First

- `src/dander/cli/azure_command.py`
- `tests/cli/test_init_cli.py`
- `acceptance/cloud-portability/phase6/azure-google-federation/README.md`
