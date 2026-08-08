# Morning Handoff

## Finished

- Added a typed `WarehouseRuntime` composed from small provider capabilities.
- Registered BigQuery through a dependency-light config and lazy runtime factory.
- Routed hosted/sandbox writers and model/graph runners through the selected warehouse.
- Exposed BigQuery relation, canonical schema, fencing, telemetry, and capability adapters.
- Preserved implicit v1 and explicit v2 BigQuery selection without moving other providers.

## Try It

Run an existing v1 or v2 BigQuery project normally. `dander run` now selects the BigQuery
warehouse runtime internally; public commands and configuration are unchanged.

## Checks

- All 903 tests, Ruff, formatting, and strict mypy across 209 files passed.
- Wheel/sdist build, metadata/distribution validation, and dependency audit passed.
- The non-root/read-only full runtime image passed conformance and its BigQuery factory probe.
- Trivy found no high/critical issues; the focused secret scan passed.
- All Terraform roots passed; GCP reported `No changes` and both schedules stayed paused.

## Decisions

- Compose small capabilities instead of adding a large warehouse-provider interface.
- Keep the BigQuery implementation lazy and behaviorally unchanged behind the new factory.
- Move state, catalog, secrets, and launchers only in their own focused tickets.

## Remaining

- Open and merge the focused BigQuery runtime PR after protected CI passes.
- Route BigQuery durable state through the shared state contract next.

## Review First

- `src/dander/warehouse/runtime.py`
- `src/dander/providers/bigquery/runtime.py`
- `src/dander/cli/run_command.py`
