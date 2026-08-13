# Dander Engineering Guide

- Use Python 3.12+ through `uv`; keep `uv.lock` synchronized with `pyproject.toml`.
- Run Ruff lint/format, strict mypy, focused pytest, and proportionate full checks before merge.
- Regenerate Control contracts with `scripts/generate_control_contracts.py` and verify drift with
  `scripts/check_control_contracts.py`; never hand-edit generated schemas or fixtures.
- Keep provider SDK imports lazy and inside the selected provider boundary. Public Control models
  and GraphStore semantics must remain provider-neutral and fail closed.
- Preserve existing CLI, runtime, and cloud-provider behavior unless the ticket explicitly changes
  it. Use focused protected PRs and verify exact-main CI after merge.
- Never commit credentials, secret values, business rows, Terraform state/plan files, caches, or
  unreviewed live-provider evidence.
