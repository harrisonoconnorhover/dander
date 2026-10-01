# Morning Handoff

## Finished

- Published immutable RC34 Control with graph previews, recorded explanations, and selected-date repair; verified the paired RC33 worker image.
- Published Salesforce `0.3.2` and ServiceNow `0.2.3` with corrected compatibility and current catalog/starter pins.
- Published Druff `0.2.0-rc.1` after protected PR #25 and exact-main checks, generated from the exact published RC34 wheel. Its guided configure → preview → save/run → understand/recover flow is ready for the documented hosted setup.
- Completed a distinct native BigQuery/Cloud Run repair and actual Druff client observation; cleaned both temporary proof environments.
- Updated the setup guide, connector documentation, support boundaries, and delivery records.

## Try It

Follow the [guided workflow guide](docs/guided-workflow.md) for the exact RC34 Control wheel, RC33 worker, and hosted Druff setup. Use the configured OIDC deployment and matching bootstrap descriptor. Preview changes, save the reviewed revision, then run it. Repair's end date is excluded.

## Checks

- Backend exact-main [CI 36874195042](https://github.com/harrisonoconnorhover/dander/actions/runs/36874195042) passed: 2,528 tests, 516 strictly typed files, Ruff, contracts, distribution, infrastructure, container and secret checks. One existing test-client deprecation warning remains.
- Public wheel bytes and GitHub attestations verified; fresh installation checked all 45 contract files. Published connector packages passed 93 Salesforce and 23 ServiceNow tests against RC34, plus earlier supported-version checks.
- RC33 worker startup/shutdown, provider imports, vulnerability scan, and anonymous digest pull passed. Local secret-pattern matches were verified Oracle SDK examples.
- Druff passed 685 unit tests, nine artifact tests, 11 browser journeys, RC34 HTTP acceptance, and the real native-run client observation. Browser OIDC/API fixtures were synthetic.
- Druff exact-main [CI 36877515144](https://github.com/harrisonoconnorhover/druff/actions/runs/36877515144) passed, including both architecture scans and build reproducibility.
- Published Druff image bytes matched the verified OCI index through anonymous registry access; both architectures passed source-free, non-root, read-only runtime and scan checks. Existing active/rollback aliases were preserved.
- Native repair recorded two rows written, four affected, and zero ingestion rows; surrounding/raw data and watermark unchanged, one execution, original Job restored. Cloud cleanup passed before its deadline; local databases, processes, watchdogs, and task credentials were removed.

## Decisions

- Measurements come from recorded results; missing values stay Unknown.
- Repair uses currently retained raw data and commits per output.
- Deliver an experimental matched candidate; public PyPI remains RC20.

## Remaining

- Production identity-provider, scale, additional-provider, and broader release gates remain separate.
- Keep the USD 1 proof reservation within the USD 21 October aggregate while billing settles.

## Review First

- [Exact artifacts and guided setup](docs/guided-workflow.md)
- [Repair behavior and limits](docs/output-repair.md)
- [Druff PR #25](https://github.com/harrisonoconnorhover/druff/pull/25)
