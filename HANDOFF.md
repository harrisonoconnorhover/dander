# Morning Handoff

## Finished

- Replaced the hard-coded Spark qualification rows/output with one reusable linear graph runtime.
- Bound canonical graph, content-addressed configuration, physical plan, and immutable driver pair.
- Added the existing Greenhouse graph's semantics-preserving transform for fused/Spark parity.
- Preserved Control result parsing, fixed two-executor shape, and verified exchange cleanup.

## Try It

Compile the Greenhouse graph with Fargate and Dataproc profiles. For Dataproc, upload the canonical
runtime configuration to `gs://<staging-bucket>/config/<sha256>.json`; Control binds the expected
graph SHA automatically.

## Checks

- Repository-wide Ruff lint and formatting passed: 527 files.
- Strict repository typing passed for 471 source files.
- Full Pytest suite and Control contract validation passed; only the existing Starlette warning.
- Both adversarial reviews' material findings were corrected; the two-pass cap forbids a third.

## Decisions

- The supported subset is direct type-preserving mappings and one unpartitioned BigQuery replace.
- Both qualification images must come from the same exact-main commit.
- Live parity is exactly Fargate then Spark against one raw BigQuery snapshot.

## Remaining

- Open one functional PR, require protected CI, merge, and confirm exact-main CI.
- Publish the exact-main image pair and run the two-cell parity qualification, then clean up.

## Review First

- `scripts/spark_driver.py`
- `src/dander/control/execution_plan_compiler.py`
- `tests/test_spark_driver.py`
