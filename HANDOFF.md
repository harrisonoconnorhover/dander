# Morning Handoff

## Finished

- Replaced Fargate's one-time ECS credential copy with a renewable Google Auth supplier.
- Scoped cross-cloud credentials to one OCI runtime invocation and explicit GCP client creation.
- Removed task secrets from global environment/file persistence in normal runtime execution.
- Raised the validated Fargate deadline from one hour to 24 hours.
- Kept Cloud Run/local ADC behavior and the public Fargate support boundary unchanged.

## Try It

Run `uv run pytest tests/identity/test_aws_google.py tests/providers/test_launcher_runtime.py -q`.

## Checks

- All 1,117 tests passed against PostgreSQL 15.
- Ruff, formatting, and strict mypy passed across `src` and `tests`.
- AWS root/module Terraform formatting, validation, and the Fargate module test passed.
- Wheel/sdist inspection and a source-free wheel installation outside the checkout passed.
- The full-runtime container passed read-only conformance; Docker Scout found no fixable high or critical vulnerabilities.

## Decisions

- Google Auth refetches only the validated ECS relative-credential endpoint on each subject-token refresh.
- Existing Google clients receive the scoped credential explicitly; non-Fargate ADC remains unchanged.
- Renewable identity is a prerequisite, not a Fargate support promotion.

## Remaining

- Let protected CI repeat Linux packaging, Terraform, container, and secret checks.
- Merge the focused PR through protected main after review.
- Publish the next candidate before a source-free Fargate pipeline acceptance run.
- Complete replay, interruption, schedule, alert, rollback, and no-drift acceptance.

## Review First

- `src/dander/identity/aws_google.py`
- `src/dander/cli/runtime_command.py`
- `tests/identity/test_aws_google.py`
