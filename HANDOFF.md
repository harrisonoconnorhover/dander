# Morning Handoff

## Finished

- Closed the hosted Control API gap where graph delete, run cancel, and run replay silently ignored request bodies.
- Reused the existing bounded empty-body guard so every header-only mutation now rejects payloads before dispatch.
- Added regressions proving rejected deletes preserve the graph and rejected run mutations never reach the lifecycle port.

## Try It

Run `uv run pytest -q tests/control/test_hosted_control.py`.

## Checks

- Ruff lint/format, strict typing across 422 files, Control-contract drift, and dependency audit passed.
- Full pytest passed: 1,804 passed, 34 skipped; the skips require the unavailable local Docker/PostgreSQL service.
- All CI Terraform format/init/validate/tests and Helm lint/render checks passed.
- Wheel/sdist inspection, isolated installs, scaffold validation, full-runtime import, and scaffolded Terraform validation passed.
- Container builds/scans and local gitleaks were unavailable because Docker, Trivy, and gitleaks are not running/installed here.

## Decisions

- Kept the published v1 routes and response models unchanged; this only enforces their documented no-body contract.
- Made no Phase 8, live-provider, roadmap, ticket, support, or release changes.

## Remaining

- Let protected CI run its PostgreSQL, container, Trivy, and gitleaks jobs.

## Review First

- `src/dander/control/http.py`
- `tests/control/test_hosted_control.py`
