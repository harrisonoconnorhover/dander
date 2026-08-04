# Morning Handoff

## Finished

- Merged Salesforce Bulk API 2.0 through protected PR #54 after all five required CI jobs passed.
- Completed source-free Odoo and Salesforce live proofs, including replay, cursor, lease, transform/test, metadata, and soft-delete checks.
- Added sanitized proof results to operator-soak issue #26 without changing any retained schedule.
- Prepared `0.3.0rc1` release metadata and notes from merged `main`; `src/dander` is unchanged.
- Kept NetSuite explicitly simulator-validated rather than claiming real-tenant support.

## Try It

```bash
uv run dander --version
uv build --out-dir /tmp/dander-v030rc1
uv run python scripts/check_distribution.py /tmp/dander-v030rc1/*.whl /tmp/dander-v030rc1/*.tar.gz
```

## Checks

- Ruff lint/format and strict mypy passed; all 677 tests passed.
- Lock validation and the locked dependency audit passed with no known vulnerabilities.
- Terraform formatting and both repository roots validated with backends disabled.
- Wheel and sdist identity/archive checks passed; both installed outside the checkout, reported `0.3.0rc1`, and generated valid source-free projects pinned to the candidate.
- The local Linux image built and passed CLI, non-root-user, and bundled-asset checks.

## Decisions

- Candidate preparation changes release metadata, assertions, notes, and handoff only; packaged runtime behavior is unchanged from merged `main`.
- Odoo is real-instance-proven; NetSuite remains simulator-validated pending one narrow tenant acceptance.
- Tagging, PyPI publication, retained deployment, and scheduler changes remain separate approval gates.

## Remaining

- Push `codex/v030rc1-release` and open a focused release-candidate PR.
- Let protected CI repeat Linux packaging, container, Terraform, dependency, and secret checks.
- Do not merge, tag, publish, or deploy the candidate without separate direction.
- Continue the existing 30-day operator soak on its current schedules.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `.github/workflows/ci.yml`
