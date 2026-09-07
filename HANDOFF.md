# Morning Handoff

## Finished

- Reconciled current support, source, release, and operator-trial status in PR #527.
- Merged PostgreSQL graph/run/schedule durability and typed startup in protected PR #528.
- Added a YAML Control profile with relative paths and explicit command-line overrides.
- Shared six S3/Azure/OCI graph metadata and journal models in PR #529, removing about 330 lines.
- Updated the everyday checkout and dependencies; preserved `tmp/` and the original HDFS branch.

## Try It

From this checkout, run `uv sync --frozen --extra dev --extra postgres`, then
`uv run dander control serve --profile examples/control/local.yaml`. Stop with Ctrl-C.
See `docs/control-profiles.md` for PostgreSQL startup with existing plans.

## Checks

- The complete Python suite passed: 2,280 tests, with one existing Starlette warning. This included
  64 focused lifecycle/PostgreSQL tests against disposable PostgreSQL 17; CI also uses PostgreSQL 15.
- All 181 combined storage, startup, and CLI regression tests passed with colored output.
- Repository Ruff lint/format, strict typing (490 files), Control contract drift, documentation
  links, and diff whitespace checks passed.
- The everyday checkout served `/v1/projects` using the example profile. The test server was
  stopped and the disposable PostgreSQL container removed.
- All six checks passed for PRs #528 and #529. Post-merge startup CI passed at `9fdbaa3`;
  [combined main CI](https://github.com/harrisonoconnorhover/dander/actions/runs/34152279904)
  passed all six jobs at `26f1fcf`.

## Decisions

- Keep one Control process per run-store schema; do not import HDFS-only placement contracts.
- Reuse CLI validation and existing canonical plan/binding files for profiles.
- Preserve the remaining HDFS branch and the completed GCP trial evidence.

## Remaining

- Identify an existing secure Hadoop environment and access. Then integrate the needed HDFS/YARN
  slice and run the bounded workflow plus restart, cancellation, recovery, and credential checks.

## Review First

- `src/dander/control/startup_factory.py`
- `src/dander/cli/control_profile.py`
- `src/dander/control/object_graph_records.py`
