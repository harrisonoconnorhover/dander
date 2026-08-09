# Morning Handoff

## Finished

- Reproduced the public `0.8.0rc2` Fargate apply failure in the isolated proof account.
- Replaced the invalid SNS topic-policy wildcard with topic-scoped SNS actions.
- Added a focused regression assertion for the rendered Fargate Terraform.
- Kept runtime behavior, provider support, schedules, and retained GCP resources unchanged.

## Try It

Run `uv run pytest -q tests/infra/test_fargate_runtime.py`, then validate `infra/aws` with Terraform.

## Checks

- Focused Fargate tests passed: 7 tests.
- Ruff lint/format and strict MyPy passed.
- Full Python suite passed: 1,104 tests, 13 skipped.
- AWS Terraform formatting, initialization, validation, and tests passed.
- Wheel/sdist inspection and source-free scaffold verification passed.

## Decisions

- The restricted EventBridge `sns:Publish` policy remains unchanged.
- A replacement release candidate is required because rc2 cannot finish a fresh AWS apply.

## Remaining

- Merge this fix through protected main after review and CI.
- Prepare and explicitly approve a replacement release candidate.
- Re-plan the partial proof stack and require only the expected remaining resource.
- Record replay, interruption, scheduling, alert, rollback, cleanup, and no-drift evidence.

## Review First

- `infra/aws/modules/fargate/main.tf`
- `tests/infra/test_fargate_runtime.py`
