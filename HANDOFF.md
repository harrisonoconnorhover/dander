# Morning Handoff

## Finished

- Audited every AWS D7 Terraform resource and data source against provider 6.60.0 call paths.
- Added only the missing ECS deployment-status and IAM role-tag reads.
- Scoped all three reads to disposable D7 service, deployment, and role ARNs.
- Added focused structural assertions that keep the reads out of the storage policy.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_admin.py` to verify the reviewed permission map and
its existing blocking policy-size limits.

## Checks

- Focused AWS bootstrap tests passed: 15 tests.
- Terraform format, validation, and mock-provider apply test passed.
- Focused Ruff format/lint and mypy passed.
- Pending exact rendered-policy size and Access Analyzer validation before AWS apply.

## Decisions

- Add only calls deterministically exercised by the locked provider and exact configuration.
- Keep provider reads resource-scoped when AWS supports that boundary.
- Retain one disposable canary apply for behavior documentation cannot prove.

## Remaining

- Finish checks, protected review, merge, and exact-main CI.
- Render and validate exact policies with AWS Access Analyzer.
- Apply the reviewed role-policy correction and verify stage-zero no-drift.
- Replan and finish the tracked AWS foundation through the temporary deployment role.
- Continue the disposable D7 live proof, rollback, and exact cleanup.

## Review First

- `infra/aws/bootstrap-admin/main.tf`
- `tests/bootstrap/test_aws_admin.py`
- `tickets/DANDER-131-aws-control-plane-deployment.md`
