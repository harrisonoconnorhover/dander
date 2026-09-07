# Morning Handoff

## Finished

- Reconciled current support, source, release, and operator-trial status in PR #527.
- Integrated PostgreSQL graph/run/schedule durability and typed startup from the preserved branch.
- Added a YAML Control profile with relative paths and explicit command-line overrides.
- Routed Control validation errors through Typer's normal terminal formatter.

## Try It

From this checkout, run `uv sync --frozen --extra dev --extra postgres`, then
`uv run dander control serve --profile examples/control/local.yaml`. Stop with Ctrl-C.
See `docs/control-profiles.md` for PostgreSQL startup with existing plans.

## Checks

- 64 focused lifecycle and PostgreSQL tests passed against disposable local PostgreSQL 17.
- 23 profile and Control CLI tests passed.
- Repository Ruff lint/format, strict typing (489 files), Control contract drift, documentation
  links, and diff whitespace checks passed.
- Broader implementation checks and protected-main verification follow this integration.

## Decisions

- Keep one Control process per run-store schema; do not import HDFS-only placement contracts.
- Reuse CLI validation and existing canonical plan/binding files for profiles.
- Preserve the remaining HDFS branch and the completed GCP trial evidence.

## Remaining

- Complete protected integration and exact-main CI.
- Consolidate identical object-store record and journal definitions separately.
- Identify an existing secure Hadoop environment and access for enterprise qualification.

## Review First

- `src/dander/control/startup_factory.py`
- `src/dander/cli/control_profile.py`
- `docs/control-profiles.md`
