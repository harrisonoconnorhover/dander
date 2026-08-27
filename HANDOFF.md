# Morning Handoff

## Finished

- Kept Dander's canonical Spark image identity pinned to its OCI digest.
- Added a revision-covered provider image tag constrained to the same Artifact Registry package.
- Made the Managed Spark adapter submit and validate Google's documented tagged image reference.
- Added focused coverage for the provider tag, package mismatch, lifecycle, and AWS Control input.

## Try It

Register `spark.container_image_tag` beside the existing three Spark extensions. Use a repository
with immutable tags and verify that the tag resolves to the execution plan's image digest.

## Checks

- Ruff lint and format checks passed for all changed Python files.
- Focused provider, lifecycle, and AWS Control tests passed: 37 tests.
- Strict type checking and the full local test suite passed.
- Protected CI and live qualification remain pending.

## Decisions

- The execution plan digest remains Dander's canonical artifact identity.
- The provider tag is plan-revision-covered and must address the identical image package.
- Tag immutability and digest resolution are publication and qualification gates.

## Remaining

- Merge through protected CI and confirm exact-main CI.
- Publish the exact-main pair to a tag-immutable repository.
- Run the single Control to Managed Spark to BigQuery qualification and capture cleanup evidence.

## Review First

- `src/dander/providers/dataproc_serverless/operations.py`
- `src/dander/control/dataproc_serverless_execution_backend.py`
- `tests/control/test_dataproc_serverless_execution_backend.py`
