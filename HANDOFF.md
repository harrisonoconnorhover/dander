# Morning Handoff

## Finished

- Added the one provider-required IAM read that blocked deletion of three empty D7 roles.
- Kept the permission limited to existing `dander-d7-*` role ARNs with no instance-profile mutation.
- Updated focused policy coverage and the AWS stage-zero/live-attempt documentation.
- Preserved the partially destroyed application state so cleanup can resume after the policy merges.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_admin.py` and
`terraform -chdir=infra/aws/bootstrap-admin test -no-color`.

## Checks

- Fifteen focused AWS administrative-bootstrap tests passed.
- Terraform format, initialization, validation, and the stage-zero Terraform test passed.
- Ruff, Terraform formatting, and `git diff --check` passed.

## Decisions

- Treat `iam:ListInstanceProfilesForRole` as required cleanup behavior observed from provider 6.60.0.
- Add no instance-profile write action and make no application-root change.
- Resume the tracked destroy only after protected merge and a reviewed stage-zero plan/apply.

## Remaining

- Complete independent review, protected CI, and merge this focused correction.
- Validate the rendered policy with IAM Access Analyzer and apply only the reviewed stage-zero plan.
- Replan and finish the application destroy, then verify exact AWS absence and no drift.
- Remove the disposable synthetic GCP issuer and finish the D7 evidence closure PR.

## Review First

- `infra/aws/bootstrap-admin/main.tf`
- `tests/bootstrap/test_aws_admin.py`
- `tickets/DANDER-131-aws-control-plane-deployment.md`
