# Morning Handoff

## Finished

- Ran the protected RC28 Azure correctness objective after all canonical preflights passed.
- Classified Snowflake error 904 as a deterministic portable-identifier application defect.
- Preserved the failed execution; automatic retry and the success-conditional replay did not run.
- Removed all active Azure, Snowflake, PostgreSQL, and disposable credential resources.
- Protected the focused identifier fix in PR #360 as main `a2b72f8`; exact-main CI passed.

## Try It

Review the sanitized attempt record and protected fix. Do not rerun RC28; prepare and publish a
replacement candidate from exact protected main.

## Checks

- Protected objective exact-main CI passed all five jobs in run `31981210288`.
- Canonical Azure/Snowflake/PostgreSQL preflight passed before the one manual execution.
- Exact cleanup inventory passed; no kind cluster exists and only the inactive Key Vault tombstone
  remains.
- Azure ActualCost returned no posted rows; the USD 2 conservative bound remains held.
- JSON, whitespace, Ruff, strict typing, contract drift, and full pytest checks passed.
- Container repair exact-main CI run `31986274883` passed all five jobs.
- Snowflake correction exact-main CI run `31987252875` passed all five jobs.

## Decisions

- Treat the failure as deterministic application behavior, not provider, setup, credential, or
  operator-tooling failure.
- Keep RC28 immutable and automatic retries disabled; publish a protected replacement candidate.
- Preserve unaffected accepted evidence and rerun only the Azure correctness lane.

## Remaining

- Complete protected review and merge this focused attempt record.
- Publish the replacement candidate and rerun only Azure correctness within the remaining cap.
- Complete provider scale/cost, pairwise, soak, audit, and closure gates.

## Review First

- `docs/evidence/phase8/2026-08-17/azure-snowflake-rc28-correctness-retry-attempt.json`
- `tickets/DANDER-214-snowflake-portable-identifiers.md`
- `docs/cloud-portability-phase8-qualification.md`
