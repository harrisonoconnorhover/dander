# Morning Handoff

## Finished

- Added bounded size-class selection across immutable registered Fargate and Cloud Run plans.
- Added API selection by explicit `size_class` or bounded `estimated_input_bytes`, with a configured default.
- Persisted versioned sizing evidence in run-record v4 and exposed it through Control status.
- Preserved v1-v3 recovery, schedules, replay behavior, and fixed single-plan deployments.
- Rendered size candidates and the default class through existing AWS Control startup arguments.

## Try It

Repeat `--run-size-candidate REVISION,CLASS,MAX_BYTES`, set `--run-default-size-class`, then start a run with either `?size_class=small` or `?estimated_input_bytes=5000000`.

## Checks

- Full pytest with all extras: passed.
- Ruff and strict type check across 461 source files: passed.
- Generated and validated the Control contract bundle; focused Control, CLI, serialization, scheduling, AWS, and Kubernetes tests passed.

## Decisions

- Size classes map only to pre-registered immutable plans; the selected plan remains the resource source of truth.
- Size filtering happens before the existing cost/locality placement and chooses the smallest fitting class per environment.
- Sizing evidence does not alter exact-plan idempotency; the plan revision already captures execution resources.

## Remaining

- Open the protected DANDER-240 PR, merge after required checks, and confirm exact-main CI.
- Begin physical-plan v1 only after DANDER-240 closes.

## Review First

- `src/dander/control/run_lifecycle.py`
- `src/dander/control/orchestration.py`
- `src/dander/control/orchestration_serialization.py`
