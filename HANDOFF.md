# Morning Handoff

## Finished

- Ran exact-main RC32 C20 once; readiness and all probes passed, but the non-superuser runtime role could not observe the superuser-only usage view.
- Captured one post-terminal namespace-creator read: 3,840 charged seconds, 268 compute seconds, 8 RPU, USD 0.40, and zero failure schemas.
- Removed the launcher first, destroyed all 37 data-plane resources, purged exact state/lock history, and verified empty direct inventories.
- Added a C21 objective that emits a cleanup-verified interim, performs one post-terminal privileged read, and finalizes offline in immutable RC32 without repeating the workload.

## Try It

Run `uv run python scripts/validate_redshift_objective.py docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-external-cost-objective.json --smoke-image 184463061564.dkr.ecr.us-east-1.amazonaws.com/dander:0.9.0rc32`.

## Checks

- 85 focused benchmark, launcher, and validator tests passed.
- Ruff lint/format and strict typing across 442 source files passed.
- C21 objective validation, JSON parsing, and immutable ARM64 RC32 container smoke passed.
- Provider-measured C20 spend leaves USD 0.008580237206 after the full C21 reservation.

## Decisions

- Preserve C20 as failed because no normalized report was emitted; its workload and cleanup evidence passed.
- Follow AWS's documented superuser boundary instead of elevating the runtime task role.
- Keep one workload task, one post-terminal usage query, zero workload reexecutions, the 2 RPU-hour guard, and the USD 0.75 reservation.

## Remaining

- Protect the C20 result, external finalizer, and C21 objective through PR checks and exact-main CI.
- Run one fresh C21 execution, finalize its report from one post-terminal provider read, and clean up immediately.
- Close the Redshift failure cell only if the protected report passes and cleanup remains empty.

## Review First

- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-quiet-metadata-result.json`
- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-external-cost-objective.json`
- `scripts/benchmarks/redshift_failure_phase8.py`
