# Morning Handoff

## Finished

- Named pipelines and connector inspection resolve project-relative files consistently, including model directories and explicit overrides.
- Normal CLI failures render concise errors with their original exit codes; incorrectly encoded configuration files identify UTF-8 as the remedy.
- Storage Write preserves canonical-schema values in protobuf rows and rejects unsupported repeated fields before opening a stream.
- Control shutdown retains dependencies while workers finish; retries close remaining resources without repeating successful cleanup.
- Failed-write observations cannot leak into successful retries; all ingestion paths share the same write-and-drain boundary.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`. From another directory, run
`dander run PIPELINE --config /path/to/project/dander.yaml --dry-run`.
For focused checks, use `uv run pytest tests/cli/test_project_paths.py tests/control/test_shutdown_recovery.py tests/writer/test_storage_write_writer.py`.

## Checks

- Full suite: 2,387 tests passed with disposable PostgreSQL 15; its container was removed afterward.
- Canonical strict typing: 506 files passed. Ruff lint/format, Control contract drift, and diff checks passed.
- All 154 CLI tests passed, including actual subprocess rendering, exit codes, and import isolation.
- Schema, shutdown, telemetry-retry, and encoding failures were reproduced before fixes; their regressions now pass.
- Protected PR and exact-main results are reported with final delivery. No live provider workload was run.

## Decisions

- Resolve input paths and native schema fields at their owning boundary, then reuse them consistently.
- Keep shutdown requested distinct from cleanup completed; shared dependencies remain available until their workers stop.
- Preserve public Click error classes, legacy schema precedence, standalone source paths, and existing provider commit/retry behavior.

## Remaining

- Connector commands still need the deployment/platform selector flags already supported by run and validate.
- Active-run indexing and leaner dependency packaging remain optional migrations.

## Review First

- `src/dander/control/run_lifecycle.py`, `startup_factory.py`, and `tests/control/test_shutdown_recovery.py`
- `src/dander/cli/entrypoint.py`, `run_command.py`, and `tests/cli/test_project_paths.py`
- `src/dander/runtime.py`, `tests/test_runtime.py`, and `src/dander/writer/storage_write.py`
