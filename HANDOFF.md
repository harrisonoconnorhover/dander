# Morning Handoff

## Finished

- Prepared the fix as exact maintenance candidate `0.7.1rc1` from the accepted `0.7.0` line.
- Read the immediate BigQuery table schema and skip no-op additive DDL.
- Batch all genuinely missing nullable fields into one race-safe `ALTER TABLE` statement.
- Updated locked GitPython from `3.1.57` to `3.1.58` to clear current advisories.

## Try It

Run `uv run pytest tests/state/test_run_history.py -q`; the current-schema case emits no ALTER and
the sparse legacy case emits one bounded migration.

## Checks

- 818 tests, Ruff lint/format, and strict mypy passed.
- Dependency audit passed with no known vulnerabilities.
- Terraform platform/stage-zero validation and exact `0.7.1rc1` wheel/sdist source-free installs passed.
- Exact-candidate container build, non-root/read-only conformance, and bundled assets passed; CI
  must run Trivy and the secret scan.

## Decisions

- Release the fix from protected `release/0.7`; do not tag current main.
- Forward-port the same functional fix to main in a separate PR.
- Keep the retained project paused until a patched image passes smoke and replay.

## Remaining

- Merge the hotfix through `release/0.7` and publish `0.7.1rc1`.
- Build the patched source-free paused image and resume the retained smoke/replay sequence.
- Publish the restored-schedule image, apply it through review, and require final no drift.
- Forward-port the fix to current main.

## Review First

- `src/dander/state/run_history.py`
- `tests/state/test_run_history.py`
- `uv.lock`
