# Morning Handoff

## Finished

- Implemented an idempotent OCI Function controller with one fresh Container Instance per attempt.
- Added Resource Scheduler, pipeline-scoped lifecycle events, exact resource principals, logs, and alarms.
- Added crash-recoverable Object Storage run records plus bounded logs, interruption, replay, and retry.
- Added manifest-bound OCI run/status/logs/cancel/replay and exact launcher planning/no-drift inputs.
- Added runtime resolution of versionless OCI Vault references and a source-free controller image.

## Try It

Run `uv run pytest tests/providers/test_oci_container_instances_controller.py`.

## Checks

- Complete pytest, Ruff lint/format, and repository-wide Mypy pass.
- Both OCI Terraform roots validate; their native tests pass (2 foundation/controller, 1 stage zero).
- Wheel/sdist build and the distribution contract pass.
- The `linux/amd64` Function image builds and imports Dander `0.9.0rc1` with OCI SDK `2.184.1`.
- No state, plan, key, or credential file is present in the worktree diff.

## Decisions

- The Function owns parallelism one, exit-75 whole-task retry, a 3,300-second deadline, and cleanup.
- Scheduler delivery is UTC/hourly; event rules and controller dynamic groups are pipeline/exact-OCID scoped.
- Runtime task images remain source-free; the Function image contains only the exact wheel and dependencies.

## Remaining

- Merge this implementation through a protected PR and verify protected-main CI.
- Obtain explicit approval for public candidate publication and a numeric OCI per-attempt ceiling.
- Run read-only OCI preflight, reviewed plans, live profile/rotation/rollback/cleanup, and no drift.
- Merge sanitized live evidence, then make the binary Phase 7 exit-gate recommendation.

## Review First

- `src/dander/providers/oci_container_instances/controller.py`
- `src/dander/providers/oci_container_instances/oci_adapter.py`
- `infra/oci/main.tf`
