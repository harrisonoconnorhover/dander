# Morning Handoff

## Finished

- Merged the Step Functions CLI namespace correction through protected main as PR #154.
- Prepared release-only metadata for `dander-platform==0.8.0rc4`.
- Kept `src/dander`, Terraform, manifests, and provider support declarations unchanged.

## Try It

Run `uv run python scripts/check_release_metadata.py`, then build and inspect the candidate.

## Checks

- PR #154 passed Python, Terraform, packaging, container, and secret checks.
- A source-free built wheel verified both deployed Fargate controllers and paused schedules.
- Release metadata validation and focused tests passed.
- Wheel/sdist inspection and source-free installation passed outside the checkout.

## Decisions

- `0.8.0rc4` replaces rc3 for complete Fargate lifecycle acceptance.
- Fargate remains experimental until the lifecycle gate passes.

## Remaining

- Merge this release-only PR after protected checks pass.
- Tag and publish `0.8.0rc4` from the exact protected merge.
- Reinstall rc4 source-free and rerun Fargate verification and lifecycle acceptance.
- Record replay, interruption, scheduling, alert, rollback, cleanup, and no-drift evidence.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `docs/session-resume.md`
