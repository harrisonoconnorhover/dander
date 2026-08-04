# Morning Handoff

## Finished

- Updated the public install path to the latest published release, `dander-platform==0.2.0`.
- Made support, upgrade, release, known-limitations, and operator-soak wording current and minor-version-aware.
- Preserved the optional guarded-upgrade instructions while keeping the standard path free of billing-account requirements.
- Audited every remaining remote `codex/*` branch against its merged pull request and deleted all 13 stale pointers.
- Preserved intentional backup and unreviewed local branches.

## Try It

```bash
uv tool install dander-platform==0.2.0
dander new my-data-platform
cd my-data-platform && dander validate
```

## Checks

- Ruff lint/format and strict mypy passed; all 677 tests passed.
- Repository project validation passed for all five configured pipelines.
- The `0.3.0rc1` wheel and source distribution built and passed identity/archive inspection.
- The stale `0.1.x` scan now finds only intentional historical decision records.
- Post-merge CI for `0.3.0rc1` passed all five jobs before this documentation-only cleanup.

## Decisions

- Public onboarding remains pinned to final `0.2.0`; merged `0.3.0rc1` metadata is not presented as published.
- Support policy uses durable current-minor language instead of embedding obsolete minor-specific rules.
- Pull requests retain the history of deleted feature branches; unique local backups remain untouched.

## Remaining

- Obtain explicit approval before tagging or publishing `0.3.0rc1`.
- Continue the existing 30-day operator soak and its weekly review cadence.
- Keep NetSuite simulator-validated until one narrow real-tenant acceptance succeeds.

## Review First

- `README.md`
- `SECURITY.md`
- `docs/upgrading.md`
