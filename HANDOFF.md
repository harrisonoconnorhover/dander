# Morning Handoff

## Finished

- Reconciled retained August 27–28 Control/Fargate/Spark successes and corrected the obsolete DANDER-251 non-execution claim.
- Updated five completed tickets, Druff hosted-mode documentation, and Salesforce plugin write-command boundaries.
- Prepared Salesforce 0.3.2, ServiceNow 0.2.3, and Dander catalog/install-check patches outside this documentation change.

## Try It

Read `docs/control-execution-history.md`, then `docs/support-status.md`. Prepared package and catalog patches are in `/tmp/dander-curated-compat-20260930/`.

## Checks

- Documentation: relative links, retained output equality, source/image identities, and `git diff --check` passed.
- Prepared fixes: 93 Salesforce and 23 ServiceNow tests passed on public Dander 0.7.1 and 0.9.0rc20, current 0.9.0rc32, and each package's locked environment. Package lint, format, and typing passed.
- Prepared Dander patch: 37 focused tests, full Ruff lint/format, canonical strict typing (507 files), contract drift, and outside-checkout wheel resolution/metadata/entry-point checks passed using local candidate wheels.

## Decisions

- Retained live evidence qualifies only its named revisions and workloads; no new cloud runs or support promotion.
- Do not publish catalog pins until the corresponding packages exist on PyPI.

## Remaining

- Await the requested narrow exception to AGENTS.md's single-writable-repository rule for the two existing connector repositories.
- Then apply the prepared connector patches, complete protected CI/publication, verify PyPI, apply the Dander catalog patch, and merge with exact-main CI.

## Review First

- `docs/control-execution-history.md` and corrected support boundaries.
- `/tmp/dander-curated-compat-20260930/*-compatibility.patch` for the pending package work.
