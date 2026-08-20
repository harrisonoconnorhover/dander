# Dander Engineering Guide

- Use Python 3.12+ through `uv`; keep `uv.lock` synchronized with `pyproject.toml`.
- Run Ruff lint/format, the canonical `python3 scripts/check_types.py` strict check, focused pytest,
  and proportionate full checks before merge. Its targets live in `[tool.mypy].files`; do not
  substitute `mypy .` or recursively type-check auxiliary scripts.
- Regenerate Control contracts with `scripts/generate_control_contracts.py` and verify drift with
  `scripts/check_control_contracts.py`; never hand-edit generated schemas or fixtures.
- Keep provider SDK imports lazy and inside the selected provider boundary. Public Control models
  and GraphStore semantics must remain provider-neutral and fail closed.
- Preserve existing CLI, runtime, and cloud-provider behavior unless the ticket explicitly changes
  it. Use focused protected PRs and verify exact-main CI after merge.
- Never commit credentials, secret values, business rows, Terraform state/plan files, caches, or
  unreviewed live-provider evidence.

## Repository Safety

- The only writable repository and pull-request base is `harrisonoconnorhover/dander`.
  `WagnerJ-Dev/dander` is a read-only, fetch-only upstream and must never be pushed to or used as a
  pull-request base.
- Before every push or pull request, run `scripts/bootstrap_repository_safety.sh`. Pushes must pass
  the tracked pre-push hook and `scripts/verify_repository_target.py`; do not bypass either check.
- Create pull requests only with `scripts/create_pull_request.py`, supplying explicit `--base` and
  `--head` branches. Do not use `gh pr create` directly.
