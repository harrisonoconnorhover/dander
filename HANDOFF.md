# Morning Handoff

## Finished

- Added DANDER-230 provider-neutral `RunSubmission`, `ExecutionPlan`, and `TriggerSpec` contracts.
- Added typed hosted run, attempt, backend, store, logs, status, result, and cleanup boundaries.
- Changed `RunLifecyclePort.start` to accept one resolved submission while preserving the existing HTTP route through a resolver seam.
- Added deterministic run/attempt identities, monotonic transition rules, and dispatch recovery.
- Proved a restart adopts one provider effect after a crash before backend-handle persistence.

## Try It

Run `uv run pytest -q tests/control/test_orchestration_contracts.py tests/control/test_hosted_control.py`.

## Checks

- Focused Ruff format and lint passed for all changed Python files.
- Full Control test suite passed: 202 tests.
- Canonical type check passed: 444 source files.

## Decisions

- Promise at-least-once Control requests with idempotent provider effects, never exactly-once execution.
- Keep schedules outside immutable execution-plan identity and preserve graph/image staleness checks.
- Keep Control authoritative only for hosted runs; preserve direct CLI and single-container execution.

## Remaining

- Review and merge DANDER-230 before starting any follow-on ticket.
- DANDER-231 may add durable S3 run state only after contract approval.
- DANDER-232 through DANDER-235 remain separately bounded AWS implementation work.
- DANDER-236 GCP/BigQuery work requires a new review and must not auto-start.

## Review First

- `src/dander/control/orchestration.py`
- `tests/control/test_orchestration_contracts.py`
- `tickets/DANDER-230-control-orchestration-contracts.md`
