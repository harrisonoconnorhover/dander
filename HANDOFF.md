# Morning Handoff

## Finished

- Capped Redshift direct ingestion at its documented 1 MiB non-file limit.
- Correlated Redshift telemetry with `LAST_USER_QUERY_ID()` and `SYS_QUERY_HISTORY`.
- Added an opt-in existing-profile Redshift qualification harness with no provisioning behavior.
- Covered all writers, `SUPER`, models, graph execution, replay, stale fencing, readback, and cleanup.
- Kept Redshift experimental and documented the paid-test boundary.

## Try It

Run `uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py
tests/portability/test_redshift_qualification.py`. The opt-in live command is in
`docs/redshift.md` and requires an existing profile plus a separately approved cost ceiling.

## Checks

- Ruff, formatting, strict mypy, dependency audit, and all 1,207 tests passed with PostgreSQL 15.
- Wheel, sdist, source-free install, runtime-all install, and generated-project validation passed.
- Non-root/read-only container conformance, Terraform validation/tests, and Helm checks passed.
- Retained stage-zero plan reported `No changes.`; no apply or live mutation occurred.
- Public source-free `dander-platform==0.7.0` with the retained deployment's existing `600s`
  compatibility input produced an exact retained platform `No changes.` plan.
- The candidate plan is 0 add/5 change/0 destroy: only five job version labels (`0.7.0` to
  `0.8.0rc8`) and declared timeouts (`600s` to `300s`) differ.
- A detached `origin/main` plan reproduced those same five changes; this branch contains no GCP,
  Terraform, manifest, or deployment-file changes.
- Final adversarial review's prefix-normalization and partial-delete findings were fixed and covered.

## Decisions

- Use the existing Redshift provider/runtime contracts, not a benchmark framework.
- Mutate only one random schema and S3 prefix and remove both on every handled exit.
- Keep both provisioned and Serverless selectable without provisioning either one.

## Remaining

- Let protected CI repeat Linux checks plus unavailable local Trivy and Gitleaks scans.
- Provision or select a separately approved disposable Redshift profile for the live run.
- Preserve a sanitized live report before any support-status change.

## Review First

- `src/dander/providers/redshift/config.py`
- `src/dander/providers/redshift/session.py`
- `scripts/benchmarks/redshift.py`
