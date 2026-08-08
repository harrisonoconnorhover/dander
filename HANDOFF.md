# Morning Handoff

## Finished

- Added version-2 manifest selection for complete Fargate launcher configuration.
- Added `dander init-aws-plan` for saved, manifest-rendered plans against existing S3/DynamoDB state.
- Added `dander init-aws-apply` to apply only the previously reviewed saved plan after confirmation.
- Preserved version-1 and Cloud Run resolution behavior.
- Documented the intentionally unsupported boundary before AWS stage zero, image publication, and live proof.

## Try It

Define a version-2 Fargate deployment, then run `dander init-aws-plan --help`. Do not apply until
the AWS stage-zero and image-publication slices establish the prerequisites documented in
`infra/aws/README.md`.

## Checks

- Ruff, formatting, strict typing, and all 953 Python tests passed.
- Supported Terraform roots and feasibility roots validated; the provider-mocked Fargate test passed.
- Wheel/sdist installs, source-free generation, container conformance, dependency audit, Trivy, and Gitleaks passed.
- Credentialed read-only AWS plan: 23 add, 0 change, 0 destroy; nothing applied.
- Retained GCP platform plan reported exactly `No changes.` and left no state lock.

## Decisions

- Keep GCP data-plane access explicit while Fargate supplies the launcher.
- Use DynamoDB state locking to preserve the Terraform 1.9+ compatibility contract.
- Keep Fargate unsupported until AWS stage zero, source-free ECR publication, and live parity pass.

## Remaining

- Let protected Linux CI repeat validation and merge the focused PR if clean.
- Add least-privilege AWS stage zero and source-free ECR image publication.
- Add AWS status, logs, cancellation, replay, and verification operations.
- Review a real saved plan before requesting separate approval for any AWS apply.

## Review First

- `src/dander/bootstrap/aws_terraform.py`
- `src/dander/cli/main.py`
- `tests/bootstrap/test_aws_terraform.py`
