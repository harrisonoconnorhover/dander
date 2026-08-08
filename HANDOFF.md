# Morning Handoff

## Finished

- Added dependency-light Fargate configuration and lazy launcher registration.
- Projected immutable ECR images, task-role identity, `awsvpc` placement, and CloudWatch intent.
- Preserved the BigQuery/GCP runtime command and GCP Secret Manager references.
- Rejected invalid Fargate sizing and the unsupported guarded-free-tier path before planning.
- Kept Fargate out of the supported runtime-capability manifest.

## Try It

Build the `fargate` launcher through the provider registry with a disposable AWS account, existing
subnet/security-group IDs, and a valid ECR digest. It returns an execution template only.

## Checks

- All 920 tests, Ruff, formatting, and strict mypy across 237 files passed.
- Wheel/sdist inspection, source-free installs, runtime-all assembly, and dependency audit passed.
- The non-root/read-only full runtime image passed conformance and bundled-asset checks.
- Trivy found no high/critical findings, Gitleaks found no leaks, and every Terraform root validated.
- Isolated GCP reported `No changes`; Salesforce and ServiceNow schedules remain paused.

## Decisions

- Keep this PR to projection and validation; infrastructure and lifecycle are separate slices.
- Reuse each pipeline's stable runtime identity name as its future AWS task-role name.
- Reject GCP's guarded-free-tier preflight on Fargate instead of weakening it.

## Remaining

- Merge the focused projection PR after protected CI passes.
- Add AWS secret resolution and the keyless BigQuery credential runtime.
- Add reviewed Fargate infrastructure and controller lifecycle.

## Review First

- `src/dander/providers/fargate/config.py`
- `src/dander/providers/fargate/runtime.py`
- `tests/providers/test_launcher_runtime.py`
