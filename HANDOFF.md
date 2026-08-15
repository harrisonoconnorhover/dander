# Morning Handoff

## Finished

- Closed five review corrections without touching separate DRUFF work.
- Protected head `4f63351` passed all five jobs in run `31867794981`.
- The sixth exact-head review found the baked version-one manifest ignored launcher projections.
- Commit `055e3a2` resolves legacy logical intent through an explicitly supplied deployment overlay.
- A real runtime-path regression now proves the baked manifest selects the AWS-native data plane.

## Try It

Run `uv run pytest -q tests/project/test_portable_config.py tests/cli/test_runtime_cli.py tests/cli/test_run_command.py tests/bootstrap/test_aws_terraform.py`.

## Checks

- Exact fifth-correction head `4f63351` passed all five protected jobs in run `31867794981`.
- Focused pytest passed: 51 project/runtime/run/bootstrap tests.
- Ruff passed repository-wide; strict mypy passed for the three changed Python/test files.
- `git diff --check` passed.
- Protected CI and rereview remain required on `055e3a2`.

## Decisions

- RC23's local rows/transport observation remains historical, but its threshold objective is invalid and cannot transfer.
- RC24 is blocked until protected CI and independent review pass commit `055e3a2`.
- Merge, public release, and support promotion still require separate approval.

## Remaining

- Push the legacy-manifest runtime correction to PR #291, pass protected CI, and rerun review.
- Cut one source-free multi-platform RC24 candidate within the reserved USD 0.50 only after that gate.
- Resume AWS-native correctness within its existing USD 3 allocation, then Azure/OCI and pairwise work.
- Rerun applicable RC22 classes on the final candidate and complete hosted scale/cost and pairwise profiles.
- Finish profile docs/status freeze and the retained soak through 2026-09-01.

## Review First

- `src/dander/project/config.py`
- `src/dander/project/portable_config.py`
- `tests/cli/test_runtime_cli.py`
