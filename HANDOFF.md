# Morning Handoff

## Finished

- Added the exact EC2 and ELB reads used by the locked AWS provider during D7 planning and refresh.
- Scoped deterministic S3 refresh reads to disposable D7 buckets.
- Scoped CloudWatch log-tag reads to `/dander/<name>/d7/*` log groups.
- Recorded the stopped live plan and confirmed that it created no resources or state.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_admin.py` and `terraform -chdir=infra/aws/bootstrap-admin
test -no-color` to verify the focused IAM boundary.

## Checks

- Focused AWS bootstrap tests passed: 15 tests.
- Terraform initialization, validation, and the bootstrap-admin test passed.
- Focused Ruff lint and format checks passed.

## Decisions

- Grant only provider calls proven by the stopped plan or deterministic provider refresh paths.
- Keep bucket and log reads resource-scoped; add no wildcard service-read authority.
- Leave the application Terraform root and provider resources unchanged.

## Remaining

- Merge the protected correction PR and verify exact-main CI.
- Apply the reviewed retained-role policy update and verify stage-zero no-drift.
- Rerun the AWS foundation plan through the temporary deployment role.
- Continue the disposable D7 live proof, rollback, and exact cleanup.

## Review First

- `infra/aws/bootstrap-admin/main.tf`
- `tests/bootstrap/test_aws_admin.py`
- `tickets/DANDER-131-aws-control-plane-deployment.md`
