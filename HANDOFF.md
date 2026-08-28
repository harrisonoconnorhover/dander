# Morning Handoff

## Finished

- Added deterministic small/large Managed Spark plan compilation from one graph and base template.
- Bound each immutable plan's partitions, executor count, CPU/memory, and input ceiling.
- Preserved unsized Fargate selection while routing Spark from supplied input estimates.
- Generalized the linear driver to the exact static planned shape with dynamic allocation disabled.
- Rejected executor/partition drift before provider submission.

## Try It

Call `ExecutionPlanCompiler.compile_managed_spark_size_classes` with bounded class definitions, pass
its plan JSON and candidate specs to the existing Control renderer, then start a Spark run with
`estimated_input_bytes`.

## Checks

- Repository-wide Ruff lint and formatting passed: 527 files.
- Strict repository typing passed for 471 source files.
- Full Pytest suite, 62 focused tests, and Control contract validation passed.
- The single final adversarial review passed with no material findings.
- The existing Starlette deprecation warning is unchanged.

## Decisions

- Sizing selects from a supplied byte estimate; Dander does not measure input yet.
- Worker shapes stay static per immutable plan and Spark dynamic allocation remains off.
- An unsized Fargate route ignores Spark's configured default class unless sizing is requested.

## Remaining

- Open and merge one protected functional PR; confirm exact-main CI.
- Publish one exact-main Spark image/driver pair.
- Run and clean up exactly two controlled-estimate Spark cells.

## Review First

- `src/dander/control/execution_plan_compiler.py`
- `src/dander/control/run_lifecycle.py`
- `scripts/spark_driver.py`
