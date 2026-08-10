# Morning Handoff

## Finished

- Reproduced an rc6 live Fargate task failure whose nonzero container exit was misclassified as a control-plane failure.
- Routed exact `States.TaskFailed` ECS payloads through the existing exit-code classifier while preserving all other controller-failure handling.
- Added rendered-controller assertions for error dispatch, payload validation, and normalized task results.

## Try It

Run `terraform -chdir=infra/aws/modules/fargate init -backend=false`, then `terraform -chdir=infra/aws/modules/fargate test`.

## Checks

- Ruff lint/format and strict typing across 304 files passed.
- The complete Python suite passed; focused Fargate tests passed 2/2 and focused operations tests passed 18/18.
- Root, stage-zero, AWS, Fargate, and portability Terraform validation passed.
- Wheel/sdist inspection and source-free installation passed; the installed scaffold contains the corrected controller.

## Decisions

- `ecs:runTask.sync` reports nonzero runtime exits as `States.TaskFailed`; these are runtime results only when the cause contains the expected ECS task fields.
- Nonmatching task errors remain fail-closed as `launcher_control_plane_failed`.

## Remaining

- Run protected CI and merge the focused fix.
- Publish a replacement candidate; rc6 must not be promoted.
- Correct the external credential-refresh proof fixture, then restart overlap, refresh, interruption, scheduling, alert, rollback, cleanup, and no-drift acceptance.

## Review First

- `infra/aws/modules/fargate/main.tf`
- `infra/aws/modules/fargate/tests/fargate.tftest.hcl`
- `tests/infra/test_fargate_runtime.py`
