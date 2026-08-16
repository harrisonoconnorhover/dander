# Morning Handoff

## Finished

- Merged concurrency evidence PR #343 as protected main `bd7489d`; exact-main run
  `31948875002` passed all five jobs.
- Moved the protected concurrency run's isolated TLS/operator package to Trash.
- Bound the next Kubernetes crossover class to exact private RC27 and `kubernetes_portable`.
- Preserved the corrected 1/10/100/1,000/5,000-row COPY/DIRECT workload, five repetitions,
  canonical equality, measured threshold, exact cleanup, and USD 0 ceiling.
- Kept hosted scale/cost, remaining provider cells, soak, public RC20, support, and DRUFF unchanged.

## Try It

Run `jq . docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-crossover-objectives.json`.

## Checks

- Concurrency evidence exact-main Python, secret, Terraform, distribution, and container jobs passed.
- The crossover objective matches the deterministic corrected workload and exact RC27 identity.
- Repository diff, JSON, and handoff structure checks pass.

## Decisions

- Measure both COPY and DIRECT without preselecting a nonzero threshold.
- Accept zero as the measured threshold when DIRECT has no contiguous winning prefix.
- Transfer no RC24 result; require a fresh exact-RC27 Job after protected objective CI.

## Remaining

- Merge this crossover objective after protected CI and review, then verify exact-main CI.
- Run exact RC27 crossover in a disposable kind cluster and retain sanitized evidence.
- Complete hosted Kubernetes scale/cost, remaining provider cells, and soak.
- Run the eventual final-candidate closure matrix.
- Freeze support only after the final-candidate closure matrix passes.

## Review First

- `docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-crossover-objectives.json`
- `docs/cloud-portability-phase8-qualification.md`
- `scripts/benchmarks/postgresql_crossover_phase8.py`
