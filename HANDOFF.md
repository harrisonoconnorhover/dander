# Morning Handoff

## Finished

- Added a deterministic Control planner from canonical graphs to physical-plan v1.
- Preserved fused single-container execution for valid graphs.
- Added one fixed distributed rule for a source-to-transform-to-target chain.
- Added exact execution-template binding and Managed Spark mode enforcement.
- Preserved backend selection and physical-plan identity across serialization and restart loading.

## Try It

Use `StaticPhysicalPlanner.plan(...)` with the same `PipelineGraphDocument` and either
`fused_container` or `distributed`, then pass the result through `bind_physical_plan(...)`.

## Checks

- Repository-wide Ruff lint and format checks passed: 526 files formatted.
- Repository-wide strict typing passed: 470 source files.
- Control contract validation passed.
- Full Pytest suite passed with only the existing Starlette deprecation warning.
- Protected CI remains pending.

## Decisions

- Distributed planning is exactly two fixed partitions with one object-store exchange.
- Unsupported distributed shapes and joins fail closed; fused execution remains the default path.
- Existing immutable execution plans continue to select environment and provider backend.

## Remaining

- Merge the functional PR after protected CI passes.
- Do not publish an image or run cloud qualification for this slice.

## Review First

- `src/dander/control/physical_planner.py`
- `tests/control/test_physical_planner.py`
- `docs/decisions.md`
