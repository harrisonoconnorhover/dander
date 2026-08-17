# Morning Handoff

## Finished

- Protected private RC29 candidate evidence in PR #363 as main `5ed786d`.
- Bound exact RC29 and digest `sha256:e016419f…aad54` to a fresh Azure correctness objective.
- Fixed one manual run, one success-conditional replay, zero automatic retries, and exact cleanup.
- Confirmed fresh Azure global names and recorded the current USD 0.0052728924 ActualCost row.
- Reserved a new USD 2 conservative bound without starting a provider mutation.

## Try It

Inspect and hash `docs/evidence/phase8/2026-08-17/azure-snowflake-rc29-correctness-objectives.json`.

## Checks

- Candidate evidence PR #363 passed all five protected jobs before merge.
- RC29 publication exact-main CI run `31988620430` passed all five jobs before publication.
- Objective JSON parses and its canonical configuration hash matches the recorded SHA-256.
- New ACR, storage-account, and Key Vault names are available; the resource group is absent.
- Azure cost access passes; delayed posting keeps conservative bounds in force.

## Decisions

- Use a new RC29 namespace; do not reuse RC28's destroyed data plane or rerun RC28.
- Hold prior USD 2, candidate USD 0.25, and new USD 2 bounds, leaving USD 5.75 unreserved.
- Allow one manual RC29 run and only its success-conditional replay; keep automatic retries off.

## Remaining

- Finish candidate-evidence exact-main CI independently.
- Protect this RC29 objective through focused review, merge, and exact-main CI.
- Recheck private coordinates, billing, credentials, and exact image before mutation.
- Run one manual candidate and only its success-conditional replay, then clean up exactly.
- Complete remaining Phase 8 provider, scale, pairwise, soak, audit, and closure gates.

## Review First

- `docs/evidence/phase8/2026-08-17/azure-snowflake-rc29-correctness-objectives.json`
- `tickets/DANDER-217-bind-rc29-azure-canonical-correctness.md`
- `docs/cloud-portability-phase8-qualification.md`
