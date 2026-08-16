# Morning Handoff

## Finished

- Added a read-only Snowflake staging-authority check to the Azure canonical preflight.
- Required the active role, database, and warehouse plus the exact database-level `CREATE SCHEMA`
  grant before any candidate job may start.
- Sanitized success and failure output so tokens, DSNs, grant rows, and provider errors stay out.
- Documented the narrow disposable-database grant and its pre-candidate operating order.

## Try It

Project a current token through the manifest's configured environment name, then run `dander azure
canonical-preflight --deployment azure_snowflake --pipeline warehouse_fixture --expected-image
ACR_IMAGE_AT_DIGEST`. A missing grant fails before any Container Apps execution.

## Checks

- Ruff format and lint passed for all changed Python files.
- Canonical strict type check passed for 421 source files.
- Full Python suite passed: 1,748 passed and 34 skipped.
- Control contract drift check passed for `io.dander.control.contracts/v1`.
- `git diff --check` passed.

## Decisions

- Extend the mandatory canonical preflight instead of adding an optional side command.
- Grant only `CREATE SCHEMA` on the named disposable database; do not grant database ownership,
  `ALL PRIVILEGES`, or account-level authority.
- Preserve RC28 evidence; this rail does not authorize a corrective rerun or transfer a result.

## Remaining

- Pass protected PR review and exact-main CI before any replacement objective or provider mutation.
- Close DANDER-213 only after that protected evidence exists.
- Bind a fresh Azure objective only after remaining budget headroom is known.
- Resume the manual/replay correctness lane without rerunning unaffected publication evidence.
- Complete remaining Phase 8 provider/profile, scale, soak, cost, and final-candidate gates.

## Review First

- `src/dander/providers/snowflake/preflight.py`
- `src/dander/cli/azure_command.py`
- `tickets/DANDER-213-snowflake-create-schema-preflight.md`
