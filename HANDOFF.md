# Morning Handoff

## Finished

- Added environment-aware plan selection to the existing Control run API without changing its default.
- Added a deterministic Cloud Run Job backend for dispatch/adoption, observation, logs, cancellation, and cleanup truth.
- Composed Fargate and Cloud Run plans in one durable Control lifecycle and schedule path.
- Added keyless AWS-task-role to GCP identity and provider-scoped reusable Terraform.
- Preserved direct CLI execution, pipeline logic, and the existing single-container worker.

## Try It

POST the existing run route with `?environment=gcp`; omit the query for the configured default.

## Checks

- Full pytest: 2,075 passed, 35 skipped.
- Ruff lint/format, canonical strict typing across 459 files, and Control contract drift: passed.
- Root and AWS Control Terraform validate; AWS Control Terraform tests: 5 passed.
- Wheel/sdist build, release metadata, and distribution validation: passed.

## Decisions

- Environment is the API selector; schedules already carry an exact immutable plan revision.
- Cloud Run Job start tokens provide deterministic provider identity across retries and restarts.
- AWS-to-GCP workload identity grants no stored key and leaves worker secrets with the existing Job.

## Remaining

- Open the single DANDER-236 functional PR and require protected checks.
- Merge and confirm exact-main CI.
- Publish one immutable image to both provider registries without rebuilding it.
- Run the combined AWS/Redshift and GCP/BigQuery Control matrix, capture sanitized evidence, and clean up.

## Review First

- `src/dander/control/cloud_run_execution_backend.py`
- `src/dander/control/run_composition.py`
- `infra/modules/aws-control-wif/main.tf`
