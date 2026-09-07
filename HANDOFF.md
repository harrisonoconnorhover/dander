# Morning Handoff

## Finished

- Added ambient Google credentials for standalone PostgreSQL Control.
- Preserved configured Fargate federation and sanitized credential failures.
- Confirmed the retained five GCP schedules remain paused.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`, then
`uv run dander control serve --profile examples/control/local.yaml` for the local API.
See `docs/control-profiles.md` for Google identity and PostgreSQL setup.

## Checks

- 44 focused identity and Google backend tests passed.
- Ruff lint/format and strict typing passed across 492 source files.
- Existing Google ADC refreshed successfully. Live job metadata and historical completion logs
  were inspected without starting a provider workload.

## Decisions

- Keep explicit or partial AWS federation configuration on its existing strict path.
- Use a temporary current-source worker for live acceptance: retained RC22 logs lack the telemetry
  required by current Control. Preserve the completed operator trial.

## Remaining

- Protected merge and exact-main CI for the credential fix.
- Complete DANDER-283 output, restart/adoption, cancellation, and cleanup checks.
- Publish the tested profile preparation example.
- Secure Hadoop work still needs an existing cluster and access details.

## Review First

- `src/dander/identity/control_google.py`
- `tests/identity/test_control_google.py`
- `tickets/DANDER-283-postgresql-gcp-workflow.md`
