# Morning Handoff

## Finished

- Added the deterministic digest-only D7 local Compose projection and packaged assets.
- Reused D6 OIDC, service-command, and local GraphStore contracts without another config source.
- Added a loopback HTTPS edge and non-root/read-only Control and Druff services.
- Added a networkless CHOWN-only initializer for the persistent GraphStore volume.
- Added exact active/rollback preflight and bounded live verification.

## Try It

Run `uv run pytest -q tests/deployment/test_local_compose.py`; then follow `infra/local/README.md`
after exact current Dander and Druff images are available.

## Checks

- Full pytest passes: 1,671 tests collected; focused local projection/verifier tests pass: 6 tests.
- Ruff and format pass for 433 files; focused strict mypy passes.
- Docker Compose renders twice to the same SHA-256 and preflight passes with sanitized inputs.
- Wheel/sdist build and distribution validation pass with the packaged local assets.
- Final adversarial review passes; full macOS mypy only flags four unchanged SDK adapter ignores.

## Decisions

- Local drift means equal Compose renders and stable second-up container IDs, not Terraform state.
- The only root process is a networkless one-shot initializer with CHOWN only.
- Live proof waits for exact D6/DRUFF-29 images; this profile never builds from a checkout.

## Remaining

- Merge the focused protected PR and verify exact-main CI.
- Supply reviewed immutable images, then run HTTPS/OIDC/restart/rollback/cleanup qualification.
- Continue D7 with Kubernetes only after the local gate passes.

## Review First

- `src/dander/deployment/local_compose.py`
- `infra/local/compose.yaml`
- `tests/deployment/test_local_compose.py`
