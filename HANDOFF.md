# Morning Handoff

## Finished

- Exact head `3eed46e` passed all five protected jobs in run `31870117994`.
- Eighth review accepted the flat fixture, Glue ownership, and sdist corrections, then found three successor blockers.
- Commit `9c6e27b` changes the AWS model to Redshift-supported table materialization.
- The qualification root now requires an exact candidate version for its tag and staging prefix.
- Provisioned Redshift now rejects the Serverless-only database-role field before provider access.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_qualification.py tests/providers/test_redshift_warehouse_runtime.py && terraform -chdir=infra/qualification/aws-native test`.

## Checks

- Full pytest passed with only the existing Starlette deprecation warning.
- Repository Ruff and strict mypy passed for all source plus Phase 8 harnesses.
- Recursive Terraform formatting and qualification validation passed.
- All three mocked AWS qualification plans passed, including invalid-candidate rejection.
- Wheel/sdist validation passed; the wheel contains table materialization for the AWS model.

## Decisions

- Historical RC22 AWS objectives are invalid and cannot transfer to a successor.
- Candidate identity is a required Terraform input, not an overridable caller tag.
- RC24 remains blocked until protected CI and exact-head review pass `9c6e27b`.

## Remaining

- Push the eighth correction, pass protected CI, and rerun exact-head independent review.
- Cut one private source-free multi-platform RC24 only after that gate.
- Bind a new exact AWS objective/authorization before using its retained USD 3 allocation.
- Complete final-candidate scale, cost, canonical, pairwise, and hosted Kubernetes reruns.
- Finish profile status/docs freeze and the retained soak through 2026-09-01.

## Review First

- `models/staging/stg_phase8_aws__posts.yml`
- `infra/qualification/aws-native/variables.tf`
- `src/dander/providers/redshift/config.py`
