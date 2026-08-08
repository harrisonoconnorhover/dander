# Morning Handoff

## Finished

- Added a separate packaged AWS Terraform root and product Fargate module.
- Projected immutable ECR images into non-root, read-only Fargate task definitions.
- Separated execution, per-pipeline task, controller, and scheduler IAM roles.
- Added an absolute-deadline Step Functions controller with exit-75-only retries.
- Added paused-aware scheduling, controller logs, encrypted failure queue, and a customer-key-encrypted notification topic.

## Try It

Run `terraform -chdir=infra/aws/modules/fargate test` for a provider-mocked plan. The example stack
is paused and no Terraform apply has been performed.

## Checks

- Ruff, formatting, strict typing, and all 941 Python tests passed.
- Every Terraform root validated; the provider-mocked Fargate plan passed.
- Wheel/sdist installs, source-free generation, container conformance, dependency audit, and Trivy scans passed.
- Read-only AWS plan: 23 add, 0 change, 0 destroy; schedule disabled and nothing applied.
- Existing isolated GCP platform plan reported exactly `No changes.`

## Decisions

- Use Standard Step Functions and its optimized ECS `.sync` integration.
- Retry only Dander exit code 75; keep scheduler delivery retries separate.
- Keep Fargate outside the support manifest until CLI lifecycle and live parity pass.

## Remaining

- Let protected Linux CI repeat validation and merge the focused PR if clean.
- Add manifest-aware AWS planning and CLI lifecycle in a separate slice.
- Apply only after a separately reviewed plan; then prove live Fargate parity.

## Review First

- `infra/aws/modules/fargate/main.tf`
- `infra/aws/modules/fargate/tests/fargate.tftest.hcl`
- `src/dander/providers/fargate/runtime.py`
