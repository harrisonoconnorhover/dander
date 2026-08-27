# Morning Handoff

## Finished

- Added deterministic Control placement across registered execution plans using static per-plan locality and micro-USD estimates.
- Enforced a maximum estimated cost, preferred locality, stable cost/tie ordering, and the existing `environment` query as a manual override.
- Persisted a versioned placement decision in run-record v3 and exposed it through run status after restart.
- Preserved v1/v2 run-record recovery and the existing fixed-environment behavior.
- Rendered the bounded placement policy through the existing AWS Control startup arguments.

## Try It

Start Control with `--run-environment auto`, repeat `--run-placement-candidate REVISION,LOCALITY,COST`, and supply `--run-preferred-locality` plus `--run-max-cost-microusd`. Add `?environment=gcp` to override.

## Checks

- Full pytest with all extras: passed (35 skipped).
- Ruff lint/format, strict type check across 461 source files, and diff check: passed.
- Generated and validated the Control contract bundle; focused Control, CLI, serialization, scheduling, and AWS projection tests passed.

## Decisions

- Static estimates are keyed by immutable plan revision; no pricing service or new infrastructure was introduced.
- Locality ranks first, cost ranks second inside the hard budget, and stable plan identity breaks ties.
- Placement evidence does not alter exact-plan idempotency, preserving retries of pre-v3 runs.

## Remaining

- Merge DANDER-239 after protected checks and confirm exact-main CI.
- Implement bounded single-container size-class selection next.

## Review First

- `src/dander/control/run_lifecycle.py`
- `src/dander/control/orchestration.py`
- `src/dander/deployment/aws_control_plane.py`
