# Morning Handoff

## Finished

- Merged the mandatory Snowflake staging-authority rail in PR #355 as protected main `4815561`.
- Required the active runtime role, database, warehouse, and exact database-level `CREATE SCHEMA`
  grant before any Azure candidate execution.
- Kept the preflight read-only and sanitized: no token, DSN, grant row, or raw provider error emits.
- Closed DANDER-213 after protected review and exact-main CI.
- Preserved RC28 publication and failure evidence without authorizing or transferring a rerun.

## Try It

No provider objects currently exist. A future protected objective must project a current Snowflake
token through the configured environment name, then run `dander azure canonical-preflight` before
starting its one approved candidate execution.

## Checks

- Local Ruff lint/format, strict typing, contract drift, and full suite passed: 1,748 passed,
  34 skipped.
- PR #355 run `31973525550` passed Python, Terraform, secret, container, and distribution jobs.
- Exact-main run `31973943176` passed the same five jobs for `4815561`.
- Completion review found no comments, reviews, unresolved threads, or material defect.

## Decisions

- Grant only `CREATE SCHEMA` on the named disposable database; no database ownership,
  `ALL PRIVILEGES`, or account-level authority.
- Keep Azure correctness, support, and cost open; the rail is setup evidence, not qualification.
- Require known remaining budget headroom and a fresh objective before any provider mutation.

## Remaining

- Determine exact remaining headroom under the authorized USD 10 Phase 8 ceiling.
- Bind a fresh protected Azure correctness objective only if that headroom can cover it.
- Resume manual/replay correctness without rerunning unaffected RC28 publication evidence.
- Complete provider/profile, scale, soak, cost, and final-candidate closure gates.

## Review First

- `tickets/DANDER-213-snowflake-create-schema-preflight.md`
- `docs/cloud-portability-phase8-qualification.md`
- `docs/session-resume.md`
