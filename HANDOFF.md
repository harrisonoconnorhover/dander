# Morning Handoff

## Finished

- Added the separately named hosted Control API over the provider-neutral GraphStore.
- Added configured project discovery and in-memory/rooted-local multi-graph CRUD.
- Added honest capabilities, typed catalogs, validation, preview, and normalized run routes.
- Added HTTP-safe ETags, bounded requests/responses/pages/logs, safe errors, and mutation audits.
- Kept the existing one-file loopback service unchanged and blocked external binds before OIDC.

## Try It

Run `uv run pytest -q tests/control/test_hosted_control.py`.

## Checks

- Ruff/format and strict mypy pass across 378 source files.
- Full pytest passes: 1,460 passed with 28 expected skips.
- Contract drift passes; published run schema/fixture remain byte-for-byte unchanged.
- Wheel/sdist build, release metadata, archive hygiene, and console entrypoint checks pass.
- Completion review's two blockers were corrected and their focused regressions pass.

## Decisions

- Opaque revisions use reversible strong ETags; portable identity stays in `content_sha256`.
- Lifecycle adapters own durable idempotency; unsupported operations fail closed.
- Run start uses headers; the additive source bundle remains unpublished.

## Remaining

- Merge the focused protected PR and verify exact-main CI.
- Publish the additive bundle only after separate public-release approval.

## Review First

- `src/dander/control/application.py`
- `src/dander/control/http.py`
- `tests/control/test_hosted_control.py`
