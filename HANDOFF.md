# Morning Handoff

## Finished

- Integrated protected main's D7 security-group and rule-policy corrections without changing that separate live-proof scope.
- Ran independent completion review on green PR #291; it found three material pre-candidate defects.
- Added self-scoped PostgreSQL/Redshift egress while retaining HTTPS-only public AWS egress.
- Moved bounded PostgreSQL lookahead before connection/transaction acquisition for slow sources.
- Derived crossover bytes from the writer's exact normalized size and invalidated RC23's 1,400-byte objective.

## Try It

Run `uv run pytest -q tests/providers/test_postgresql_warehouse_runtime.py tests/portability/test_postgresql_crossover_phase8_benchmark.py`, then `terraform -chdir=infra/qualification/aws-native test -no-color`.

## Checks

- The prior PR head passed all five protected CI jobs; this correction head has not run protected CI yet.
- Post-merge AWS/bootstrap/PostgreSQL/crossover/release pytest passed (29 passed, 23 provider-gated skips).
- Ruff lint and strict mypy passed for the corrected Python source; Ruff formatting is clean.
- AWS qualification and bootstrap-admin Terraform validation/tests passed (1 each); formatting is clean.
- `git diff --check` passed. Local Trivy was unavailable; protected container/security CI remains required.

## Decisions

- RC23's local rows/transport observation remains historical, but its threshold objective is invalid and cannot transfer.
- RC24 is blocked until this exact correction head passes protected CI and independent review rerun.
- Merge, public release, and support promotion still require separate approval.

## Remaining

- Push the correction/doc head to PR #291, pass all protected jobs, and rerun independent review.
- Cut one source-free multi-platform RC24 candidate within the reserved USD 0.50 only after that gate.
- Resume AWS-native correctness within its existing USD 3 allocation, then Azure/OCI and pairwise work.
- Rerun applicable RC22 classes on the final candidate and complete hosted scale/cost and pairwise profiles.
- Finish profile docs/status freeze and the retained soak through 2026-09-01.

## Review First

- `src/dander/providers/postgresql/writer.py`
- `scripts/benchmarks/postgresql_crossover_phase8.py`
- `infra/qualification/aws-native/main.tf`
