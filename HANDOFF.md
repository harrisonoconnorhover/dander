# Morning Handoff

## Finished

- Routed canonical scheduled occurrences through always-on Control and its existing durable lifecycle.
- Added encrypted standard SQS wakeups, encrypted DLQ redrive, EventBridge Scheduler, and exact IAM.
- Added occurrence idempotency, exact trigger/plan/graph validation, readiness, shutdown, and retry behavior.
- Projected canonical plans/triggers into the existing AWS Control task without changing the worker container.
- Added DANDER-234 tests, verifier coverage, operator documentation, decisions, and limitations.

## Try It

Run `uv run pytest -q tests/control/test_schedule_consumer.py tests/control/test_run_lifecycle.py` and `terraform -chdir=infra/aws-control test`.

## Checks

- Full pytest passed: 2,060 passed and 35 skipped.
- Full Ruff lint/format passed; strict typing passed for 455 source files; Control contract drift passed.
- AWS Control Terraform passed validate and 5 tests; AWS bootstrap-admin passed validate and 1 test.
- Trivy HIGH/CRITICAL infrastructure scan passed with zero findings.

## Decisions

- Use Scheduler's exact scheduled time, trigger id, and plan revision for durable occurrence idempotency.
- Delete SQS messages only after durable Control acceptance; retry failures to one shared encrypted DLQ.
- Keep one Control task, paused direct Fargate schedules, and the existing single-container worker.

## Remaining

- Review and merge DANDER-234 through protected checks and confirm exact-main CI.
- DANDER-235 live AWS/Redshift acceptance remains separate and has not started.
- DANDER-236 GCP/BigQuery remains separately reviewed and must not auto-start.

## Review First

- `src/dander/control/schedule_consumer.py`
- `src/dander/control/run_composition.py`
- `infra/aws-control/main.tf`
