# Morning Handoff

## Finished

- Merged RC24 candidate-evidence PR #299 as protected-main commit `a66ce65`; exact-main CI run `31884123337` passed all five jobs.
- Committed the corrected crossover objectives before execution and ran exact RC24 against disposable TLS PostgreSQL 15.18.
- Passed canonical equality, both-transport observation, cost, threshold, and cleanup objectives in one attempt.
- Removed the disposable schema, staging relations, database container, isolated network, and both volumes.
- Rebased on protected main `0ea4d43` without touching the separate D7 cleanup state or DRUFF work.

## Try It

Run `jq . docs/evidence/phase8/2026-08-15/postgresql-crossover.json` to inspect the normalized report.

## Checks

- Focused crossover tests passed: 7 tests.
- Exact RC24 reported all seven approved objectives passed.
- Report identity, objective/config hashes, sorted metrics, USD 0 measured cost, and SHA-256 passed validation.
- Disposable Docker resource inventory was empty after cleanup.
- JSON parsing, secret/path scan, and `git diff --check` passed.

## Decisions

- DIRECT lost at the first sampled size, so no contiguous DIRECT-winning prefix exists; keep the global threshold disabled at zero.
- The corrected ten-row fixture is 1,490 logical bytes, but equality at that size does not justify enabling DIRECT.
- This closes only corrected local crossover; it does not transfer RC22 results or promote support.

## Remaining

- Merge this focused crossover evidence after protected CI and review.
- Leave the separate D7 cleanup owner to resume its partially destroyed AWS state from protected main.
- Run AWS-native qualification from a fresh protected-main exact-objective lane after D7 cleanup cannot collide.
- Complete remaining exact-candidate scale, pairwise, hosted-cost, and canonical-profile gates.
- Finish final-candidate audit, operator docs, compatibility freeze, and soak through 2026-09-01.

## Review First

- `docs/evidence/phase8/2026-08-15/postgresql-crossover.json`
- `docs/evidence/phase8/2026-08-15/postgresql-crossover-attempts.json`
- `docs/cloud-portability-phase8-qualification.md`
