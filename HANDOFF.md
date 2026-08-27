# Morning Handoff

## Finished

- Added one Python 3.11-compatible fixed-plan Spark/BigQuery qualification driver.
- Added a separate minimal Managed Spark image without changing the Dander runtime image.
- Bound submitted and image-embedded driver bytes to one SHA-256 execution-plan argument.
- Extended AWS Control rendering to register Managed Spark through its existing GCP identity handoff.
- Added canonical plan, result collection, image UID, and vulnerability gates.

## Try It

Build `infra/spark/Dockerfile`, hash `scripts/spark_driver.py`, and register the fixed
`spark_bigquery_qualification` plan with that hash, the published image digest, and GCS driver URI.

## Checks

- Focused driver and AWS Control tests passed locally.
- Terraform validate and all six AWS Control fixture runs passed locally.
- Local amd64 image UID, driver identity, `tini`, and zero HIGH/CRITICAL Trivy gates passed.
- Full local suite passed: 2,113 tests passed and 35 skipped; protected CI remains pending.

## Decisions

- The first pair intentionally supports one fixed two-stage qualification plan only.
- GCS materializes the explicit exchange; Spark's BigQuery connector owns warehouse I/O.
- The Managed Spark image stays separate from the existing single-container runtime.

## Remaining

- Merge through protected CI, publish one exact-main pair, and run one bounded live qualification.
- Capture Control run, batch, artifact, result, cost, and cleanup evidence outside the repository.
- Leave arbitrary operators, dynamic sizing/topology, autoscaling, and cluster managers for later.

## Review First

- `scripts/spark_driver.py`
- `infra/spark/Dockerfile`
- `src/dander/deployment/aws_control_plane.py`
