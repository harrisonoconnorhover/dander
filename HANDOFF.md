# Morning Handoff

## Finished

- Exact head `0b1a8fa` passed all five protected jobs in run `31871007170`.
- Ninth review accepted the eighth corrections, then found four AWS qualification-root blockers.
- Commit `b031403` adds Redshift Serverless COPY trust and rejects invalid RDS names/CIDR ranges.
- Two size-bounded managed policies give the short-lived deployment role qualification authority.
- Protected Terraform CI now validates and runs all seven qualification-root plans.

## Try It

Run `uv run pytest -q tests/bootstrap/test_aws_phase8_qualification_policy.py && terraform -chdir=infra/qualification/aws-native test`.

## Checks

- Full pytest passed with only the existing Starlette deprecation warning.
- Repository Ruff and strict mypy passed for 414 source/test files.
- Both affected Terraform roots validate; mocked plans pass 1/1 and 7/7.
- AWS Access Analyzer found zero issues; tag-policy simulations passed and fail closed without tags.
- Wheel/sdist inspection, recursive formatting, Actionlint, and diff checks passed.

## Decisions

- Qualification authority is isolated from D7 in two customer-managed policies.
- Existing AWS stage zero needs one reviewed upgrade; later qualification uses `dander-deploy`.
- RC24 remains blocked until protected CI and exact-head review pass `b031403`.

## Remaining

- Push the ninth correction, pass protected CI, and rerun exact-head independent review.
- Cut one private source-free multi-platform RC24 only after that gate.
- Bind a new exact AWS objective/authorization before using its retained USD 3 allocation.
- Complete final-candidate scale, cost, canonical, pairwise, and hosted Kubernetes reruns.
- Finish profile status/docs freeze and the retained soak through 2026-09-01.

## Review First

- `infra/aws/bootstrap-admin/phase8-qualification.tf`
- `infra/qualification/aws-native/variables.tf`
- `.github/workflows/ci.yml`
