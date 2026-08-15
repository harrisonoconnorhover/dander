# Morning Handoff

## Finished

- Reproduced three AWS-native provider defects after applying the reviewed Phase 8 stage-zero prerequisite.
- Removed every discovery-attempt resource; RC24 state and matching AWS service inventories are empty.
- Scoped security-rule creation to its tagged parent group and Glue cleanup to tables in the exact owned database.
- Serialized the required Redshift public-`ASSUMEROLE` revoke before the runtime COPY-role grant.
- Kept the correction on a fresh protected-main branch without modifying the AWS qualification objective lane.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_phase8_qualification_policy.py` and
`terraform -chdir=infra/qualification/aws-native test`.

## Checks

- Python bootstrap-policy tests passed: 18 tests.
- AWS stage-zero Terraform validate/test passed: 1 test.
- AWS-native qualification Terraform validate/test passed: 8 tests.
- Live read-only stage-zero plan contains only two policy updates; rendered sizes are 5,676 and 3,395 characters.
- Post-cleanup state, VPC, RDS, Redshift, Glue, secret, bucket, and IAM-role inventories all returned zero.

## Decisions

- Add only AWS's provider-evaluated parent-group and Glue table-wildcard resource dimensions.
- Revoke public Redshift `ASSUMEROLE` before granting the default COPY role to `dander_runtime`.
- Treat this as a focused implementation correction; it is not AWS qualification evidence or a support pass.

## Remaining

- Merge this focused defect PR after protected CI and review.
- Apply the exact two-policy stage-zero saved plan from protected main and verify no drift.
- Rebase the separate RC24 AWS objective lane, then rerun manual/replay qualification and exact cleanup.
- Complete remaining exact-candidate scale, pairwise, hosted-cost, and canonical-profile gates.
- Finish final-candidate audit, operator docs, compatibility freeze, and soak through 2026-09-01.

## Review First

- `infra/aws/bootstrap-admin/phase8-qualification.tf`
- `infra/qualification/aws-native/main.tf`
- `tests/bootstrap/test_aws_phase8_qualification_policy.py`
