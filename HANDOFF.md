# Morning Handoff

## Finished

- Named pipelines and connector commands use consistent project-relative paths and explicit deployment/platform selection.
- Normal CLI failures render concise errors with their original exit codes; incorrectly encoded configuration files identify UTF-8 as the remedy.
- Storage Write preserves canonical-schema values in protobuf rows and rejects unsupported repeated fields before opening a stream.
- Control shutdown retains dependencies until workers finish and supports cleanup retries; S3 history pagination accepts valid short pages.
- Failed-write observations cannot leak into successful retries; all ingestion paths share the same write-and-drain boundary.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`. From another directory, run
`dander run PIPELINE --config /path/to/project/dander.yaml --dry-run`.
Use `dander connector inspect PIPELINE --config /path/to/project/dander.yaml --deployment NAME`
to inspect a selected deployment's connector.
For focused checks, use `uv run pytest tests/cli/test_project_paths.py tests/control/test_shutdown_recovery.py tests/writer/test_storage_write_writer.py`.

## Checks

- Final combined changes: 2,396 full-suite tests passed with disposable PostgreSQL 15; its container was removed afterward.
- Canonical strict typing: 506 files passed. Ruff lint/format, Control contract drift, and diff checks passed.
- All 161 CLI tests passed after adding connector selectors, including subprocess rendering, exit codes, and import isolation.
- Schema, shutdown, telemetry-retry, encoding, and S3 pagination failures were reproduced before fixes; their regressions now pass.
- Protected PR and exact-main results are reported with final delivery. No live provider workload was run.

## Decisions

- Resolve input paths and native schema fields at their owning boundary, then reuse them consistently.
- Keep shutdown requested distinct from cleanup completed; shared dependencies remain available until their workers stop.
- Preserve public Click error classes, legacy schema precedence, standalone source paths, and existing provider commit/retry behavior.

## Remaining

- Benchmark recovery against mostly completed history before adding a PostgreSQL pending-work index; no production-scale bottleneck has been established.
- Measure clean base/PostgreSQL/BigQuery installs before moving provider dependencies into existing extras; changing defaults needs a documented release.

## Review First

- `src/dander/control/run_lifecycle.py`, `startup_factory.py`, and `s3_run_store.py`
- `src/dander/cli/entrypoint.py`, `run_command.py`, and `tests/cli/test_project_paths.py`
- `src/dander/runtime.py`, `tests/test_runtime.py`, and `src/dander/writer/storage_write.py`
