# Morning Handoff

## Finished

- Added `control serve --platforms-config` and passed it through the existing hosted Fargate composition.
- Made `FargateBinding` resolve the selected deployment from the explicit operator-owned manifest.
- Validated the AWS manifest against the existing platform schema and exact plan/deployment bindings.
- Materialized the manifest through the existing read-only AWS Control config volume.
- Preserved the single-container worker, execution plan, launcher, and provider behavior.

## Try It

Run `uv run pytest -q tests/deployment/test_aws_control_plane.py tests/control/test_run_lifecycle.py tests/providers/test_fargate_operations.py tests/cli/test_control_oidc_cli.py`.

## Checks

- Focused Python tests: 40 passed.
- Full Python suite: passed with the repository's existing skips.
- Ruff lint and format: passed.
- Strict typing: passed for 455 source files.
- AWS Control Terraform tests: 5 passed.

## Decisions

- Reuse the existing platform schema, Fargate binding, config-init volume, and execution backend.
- Require platform configuration only when hosted execution plans are present.
- Keep DANDER-236, releases, RC32 evidence, and runtime behavior unchanged.

## Remaining

- Review and merge this focused DANDER-235 corrective PR after protected checks pass.
- Confirm exact-main CI, publish one new immutable image tag, and run DANDER-235 once.
- Clean up immediately and submit the single sanitized evidence/status PR.

## Review First

- `src/dander/deployment/aws_control_plane.py`
- `src/dander/cli/control_command.py`
- `infra/aws-control/main.tf`
