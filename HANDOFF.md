# Morning Handoff

## Finished

- Added a typed `StateRuntime` for leases, watermarks, run history, metadata, and migrations.
- Registered BigQuery state through a dependency-light config and lazy provider factory.
- Routed hosted state construction through the selected runtime; SQLite sandbox stays unchanged.
- Added schema ledger version 1 while preserving every existing BigQuery table identity.
- Kept server-time leases, fencing, cursor CAS, and interrupted-run reconciliation intact.

## Try It

Run an existing v1 or v2 BigQuery project normally. `dander run` selects and migrates the BigQuery
state runtime internally; public commands and authored configuration are unchanged.

## Checks

- All 907 tests, Ruff, formatting, and strict mypy across 212 files passed.
- Wheel/sdist inspection, source-free installs, runtime-all assembly, and dependency audit passed.
- The non-root/read-only full runtime image passed conformance and its bundled-asset checks.
- Trivy and Gitleaks passed; all Terraform roots validated successfully.
- Isolated GCP reported `No changes`; Salesforce and ServiceNow schedules remain paused.

## Decisions

- Preserve current BigQuery stores and table identities behind a small composed runtime.
- Record a migration only after every shared state table is ready.
- Keep per-pipeline lease-table creation lazy rather than inventing a data migration.

## Remaining

- Open and merge the focused durable-state PR after protected CI passes.
- Route Dataplex catalog publication through its provider boundary next.

## Review First

- `src/dander/state/runtime.py`
- `src/dander/providers/bigquery/state.py`
- `src/dander/cli/run_command.py`
