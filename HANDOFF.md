# Morning Handoff

## Finished

- Added a closed two-stage AWS D7 projection for foundation, active, and rollback values.
- Added the packaged CloudFront/ALB/Fargate/S3 partial-backend Terraform root.
- Added a read-only verifier for ingress policies, service/task identity, startup config, and storage.
- Added focused Python/Terraform coverage and protected-CI validation for the new root.
- Removed invalid container CPU over-reservation and locked valid task sizing in Terraform tests.

## Try It

Validate the packaged example locally with `python -m dander.deployment.aws_control_plane preflight
--input infra/aws-control/aws-control-plane.example.json --output /tmp/dander-aws-render
--terraform-root infra/aws-control`. This renders non-secret files and initializes no backend.

## Checks

- Full Python suite passed: 1,705 passed and 28 skipped; strict mypy and Ruff passed.
- Terraform format/validate/test and Trivy 0.70 passed: 4 contract runs; contract drift passed.
- Wheel/sdist inventory validation passed and includes the complete AWS root and verifier.

## Decisions

- Use one provider-issued CloudFront HTTPS origin and a CloudFront-only public ALB.
- Bind non-secret startup files into task revisions; use no config bucket or config-read identity.
- Keep this single-instance profile experimental; only Control receives S3 graph permissions.

## Remaining

- Run protected PR CI, merge, and exact-main CI.
- Reauthenticate with `aws login`; apply the already-merged stage-zero authority prerequisite.
- Run reviewed foundation/full saved plans, immutable image copy, OIDC/browser/S3 proof, and rollback.
- Destroy every disposable resource/state generation and verify retained AWS/GCP no-drift.

## Review First

- `src/dander/deployment/aws_control_plane.py`
- `infra/aws-control/main.tf`
- `tests/deployment/test_aws_control_plane.py`
