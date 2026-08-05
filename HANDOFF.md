# Morning Handoff

## Finished

- Added optional immutable Druff image input to normal initialization and full-platform previews.
- Added a public, scale-to-zero Cloud Run UI beside the hosted runtime with a dedicated no-role
  service account and ignored only the provider-reported zero scaling defaults.
- Kept graph persistence, connector data, credentials, and execution on Dander's loopback service.
- Packaged the new Terraform module into generated source-free projects.
- Documented exact image retention, public usage, and local-network behavior.

## Try It

```bash
uv run dander init --project PROJECT --container-image DANDER_DIGEST \
  --druff-container-image DRUFF_DIGEST
uv run dander graph serve --file /path/to/graph.yaml --origin HTTPS_DRUFF_URL
```

## Checks

- Full suite: 717 tests passed; Ruff and strict mypy across 163 source files passed.
- Dependency audit found no known vulnerabilities.
- Terraform format/init/validation passed for the platform, stage zero, and packaged project.
- Wheel and sdist inspection plus two outside-checkout installs/scaffolds passed.
- Dander container build/start/non-root checks passed; Docker Scout found 0 fixed high/critical issues.
- Protected CI passed, the disposable-project image-only apply succeeded, and the final Terraform
  plan reported exactly `No changes.`
- Hosted Druff opened, saved, refreshed, and validated the loopback-served Salesforce graph with
  HTTP 200 responses and no failed browser requests.

## Decisions

- Host only Druff's compiled browser shell; never publish Dander's unauthenticated graph API.
- Use one explicit digest input rather than adding a manifest mode or UI backend.
- Preserve the image in Druff-triggered full-platform previews to prevent accidental removal plans.

## Remaining

- Keep the disposable Salesforce and ServiceNow schedules paused until separately approved.
- Start the local graph service when authoring or operating a hosted graph through Druff.
- The retained proof project remains untouched.

## Review First

- `infra/modules/druff/main.tf`
- `infra/modules/druff/README.md`
- `tests/infra/test_druff_hosting.py`
