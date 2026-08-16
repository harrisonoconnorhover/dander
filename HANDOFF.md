# Morning Handoff

## Finished

- Protected the private source-free RC28 candidate record as exact main `1cdc799`.
- Bound one RC28 Azure canonical manual run and one success-conditional replay.
- Fixed the immutable GAR/ACR digest, provider profile, disposable data plane, and cleanup boundary.
- Limited infrastructure preflights to two and candidate execution to one manual run plus replay.
- Kept private coordinates and secret values outside Git under the existing USD 2 Azure allocation.

## Try It

Review `docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-objectives.json`; no live
provider action is permitted until this objective is protected and exact-main CI passes.

## Checks

- Publication PR #352 and exact-main run `31962694682` passed all five jobs.
- Objective JSON parses; its canonical configuration hash, candidate identity, and USD 2 allocation
  are checked locally.
- Azure account access is current; no provider mutation was made while preparing the objective.
- The Phase 6 PostgreSQL Keychain binding exists, but its deleted host no longer resolves.
- Central US exposes the bound PostgreSQL shape; this subscription is restricted in East US.
- Ruff, format, strict typing, contracts, release metadata, and full pytest passed; pytest reported
  1,744 passed and 34 skipped.

## Decisions

- Reuse the accepted Azure lifecycle but transfer no Phase 6 qualification result to RC28.
- Provision a fresh bounded PostgreSQL state server instead of reusing the stale Phase 6 endpoint.
- Keep cost, scale, pairwise, soak, public release, and support open after correctness execution.

## Remaining

- Protect this objective and pass its exact-main CI before provider mutation.
- Complete read-only Azure, Snowflake OAuth, and PostgreSQL plan preflights.
- Execute only the approved manual/replay correctness slice, then remove owned resources.
- Keep provider-measured cost pending until invoices post.
- Complete remaining provider/profile, scale, soak, and final-candidate gates.

## Review First

- `docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-objectives.json`
- `tickets/DANDER-211-rc28-azure-correctness-objective.md`
- `docs/cloud-portability-phase8-qualification.md`
