# Morning Handoff

## Finished

- Split security-group creation into AWS's group, creation-time tag, and VPC authorization arms.
- Kept required D7 tags on every new group and tag writes bound to `CreateSecurityGroup`.
- Added the observed internet-gateway read used while ELB creates target groups.
- Recorded the partial foundation apply; all created resources remain tracked and disposable.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_admin.py` and the bootstrap-admin Terraform test to
verify the focused IAM boundary.

## Checks

- Focused AWS bootstrap tests passed: 15 tests.
- Terraform format, initialization, validation, and the bootstrap-admin test passed.
- Focused Ruff lint and format checks passed.

## Decisions

- Follow AWS's documented dependent-resource split for tagged security-group creation.
- Keep the application Terraform root and the partially created provider resources unchanged.
- Retry through the same remote state so Terraform adopts its tracked partial apply.

## Remaining

- Merge the protected correction PR and verify exact-main CI.
- Apply the reviewed retained-role policy update and verify stage-zero no-drift.
- Replan and finish the tracked AWS foundation through the temporary deployment role.
- Continue the disposable D7 live proof, rollback, and exact cleanup.

## Review First

- `infra/aws/bootstrap-admin/main.tf`
- `tests/bootstrap/test_aws_admin.py`
- `tickets/DANDER-131-aws-control-plane-deployment.md`
