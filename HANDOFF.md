# Morning Handoff

## Finished

- Composed BigQuery metadata sizing with the existing automatic locality/cost placement path.
- Kept estimator evidence and fallback modes scoped to their exact environments.
- Added bounded cross-environment idempotency lookup before mutable metadata reads.
- Replayed only one matching automatic request and rejected manual/default or multiple claims.
- Preserved explicit routing, single-container execution, schedules, providers, and schemas.

## Try It

Run `uv run pytest -q tests/control/test_run_lifecycle.py tests/control/test_hosted_control.py`.

## Checks

- Repository-wide Ruff format and lint passed.
- Strict typing passed for 474 source files.
- Control contract drift validation passed.
- All Control tests and the full Pytest suite passed with only the existing Starlette warning.
- Final adversarial review passed after the pre-review idempotency correction.

## Decisions

- Reuse the existing immutable plans and placement cost inputs; add no new cost model.
- Search every registered exact graph environment before an automatic no-sizing retry.
- Keep provider payloads static; this slice needs no image or live-cloud qualification.

## Remaining

- Commit, push, open one protected functional PR, and merge after required checks.
- Confirm exact-main CI after merge.

## Review First

- `src/dander/control/run_lifecycle.py`
- `src/dander/control/application.py`
- `tests/control/test_run_lifecycle.py`
