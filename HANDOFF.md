# Morning Handoff

## Finished

- Prepared stable `0.7.1` from the accepted `0.7.1rc1` maintenance candidate.
- Completed retained Greenhouse, HubSpot, Salesforce, ServiceNow, and graph smoke runs.
- Verified Salesforce replay, 35 assertions, catalog publication, cursor monotonicity, and zero
  duplicate destination keys.
- Restored four tracked schedules while keeping the graph paused.
- Kept packaged runtime source unchanged from the accepted candidate.

## Try It

Build and install the distribution outside the checkout, then run `dander --version`; it should
report `0.7.1` and generate a source-free project pinned to that exact version.

## Checks

- Candidate protected CI passed 818 tests, Ruff, strict mypy, dependency audit, Terraform
  validation, packaging, container conformance/scan, and secret scan.
- Retained source-free candidate completed all five manual pipeline smokes.
- Salesforce preserved four monotonic watermarks with zero duplicate IDs; leases and run-scoped
  staging were clear after completion.
- Refreshed stage-zero and platform Terraform plans both reported `No changes.`

## Decisions

- Promote stable `0.7.1` without changing `src/dander` from the accepted candidate.
- Forward-port the run-history fix to current main separately.
- Keep provider implementations gated behind the retained GCP no-drift proof.

## Remaining

- Merge through protected `release/0.7`, tag `v0.7.1`, and publish through the protected environment.
- Verify a clean public install and deploy the stable source-free image through a reviewed plan.
- Run one stable Greenhouse smoke, restore schedules, and require final no drift.
- Forward-port the hotfix to current main.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `HANDOFF.md`
