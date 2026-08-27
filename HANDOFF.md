# Morning Handoff

## Finished

- Added a `dataproc_serverless` Control backend for distributed physical-plan v1.
- Added deterministic batch submission/adoption, restart recovery, status, logs, results, and cancellation.
- Bound immutable PySpark driver/image, GCP service account, config, secret references, and subnetwork.
- Fixed executor count/cores/memory and TTL while explicitly disabling Spark autoscaling/autotuning.
- Wired Fargate, Cloud Run, and Managed Spark through the same always-on Control lifecycle.

## Try It

Register a `dataproc_serverless` execution plan with a distributed object-store physical plan and the three `spark.*` template extensions. Start it through the existing Control run endpoint.

## Checks

- Full pytest suite: passed.
- Ruff lint and format across 522 files: passed.
- Strict type check across 467 source files: passed.
- Control contract drift and diff checks: passed.

## Decisions

- Managed Spark accepts only static distributed plans with object-store exchanges.
- The immutable driver artifact enacts the plan; Control owns provider lifecycle and durable reconciliation.
- Terminal serverless compute cleanup is confirmed while batch metadata and Cloud Logs remain evidence.

## Remaining

- Publish and live-qualify one compatible immutable Spark driver/image pair before a support claim.
- Consider dynamic physical topology/resource sizing only as a separately reviewed next milestone.
- Do not add generalized autoscaling, Kubernetes, or a cluster manager in this slice.

## Review First

- `src/dander/control/dataproc_serverless_execution_backend.py`
- `src/dander/providers/dataproc_serverless/operations.py`
- `tests/control/test_dataproc_serverless_execution_backend.py`
