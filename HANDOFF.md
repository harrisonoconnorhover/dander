# Morning Handoff

## Finished

- Reconciled current support, source, release, and operator-trial status in PR #527.
- Merged PostgreSQL graph/run/schedule durability and typed startup in protected PR #528.
- Added a YAML Control profile with relative paths and explicit command-line overrides.
- Routed Control validation errors through Typer's normal terminal formatter.
- Shared six identical S3/Azure/OCI graph metadata and journal models, removing about 330 lines.

## Try It

From this checkout, run `uv sync --frozen --extra dev --extra postgres`, then
`uv run dander control serve --profile examples/control/local.yaml`. Stop with Ctrl-C.
See `docs/control-profiles.md` for PostgreSQL startup with existing plans.

## Checks

- 64 focused lifecycle and PostgreSQL tests passed against disposable local PostgreSQL 17.
- 23 profile and Control CLI tests passed.
- 24 focused CLI tests passed with forced colored output; 135 shared/provider GraphStore tests passed.
- The combined storage, startup, and CLI regression check passed all 181 tests with colored output.
- Repository Ruff lint/format, strict typing (490 files), Control contract drift, documentation
  links, and diff whitespace checks passed.
- The complete Python suite passed: 2,280 tests, with one existing Starlette warning.
- The installed console served `/v1/projects` using the example profile; it was stopped and the
  disposable PostgreSQL container removed. Colored-output assertions were normalized for CI.
- All six protected PR #528 checks passed; exact-main and storage integration checks are pending.

## Decisions

- Keep one Control process per run-store schema; do not import HDFS-only placement contracts.
- Reuse CLI validation and existing canonical plan/binding files for profiles.
- Preserve the remaining HDFS branch and the completed GCP trial evidence.

## Remaining

- Complete protected integration and exact-main CI.
- Finish the protected shared-record integration.
- Identify an existing secure Hadoop environment and access for enterprise qualification.

## Review First

- `src/dander/control/startup_factory.py`
- `src/dander/cli/control_profile.py`
- `src/dander/control/object_graph_records.py`
