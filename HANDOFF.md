# Morning Handoff

## Finished

- Inherited DRUFF's hosted-Control qualification through PR #281 at protected main `1bbddb8` without changing it.
- Published source-free private RC22 index `sha256:ce395d…47c3`; public RC20 remains unchanged.
- Passed exact-RC22 Kubernetes lifecycle objectives on kind 1.32.2 with TLS PostgreSQL 15.18.
- Passed local PostgreSQL bounded-memory and four-pipeline concurrency objectives, retaining the failed first memory attempt.
- Passed the exact-RC22 retained GCP profile rerun: authenticated manual/replay, Scheduler execution, cleanup, and no drift.

## Try It

Inspect the GCP, Kubernetes, and PostgreSQL reports in `docs/evidence/phase8/2026-08-14/`.

## Checks

- Candidate wheel/sdist, source-free image, multi-platform registry record, SBOM, provenance, and read-only runtime inspections pass.
- Kubernetes produced seven qualified successes, one overlap skip, and one intentional interrupted failure; its cluster was deleted.
- PostgreSQL processed 2.7248 GB under 256 MiB with 176,734,208 bytes peak RSS; four pipelines processed 20,000 rows in 335.495 ms.
- GCP passed 23 deployment checks; Salesforce replay counts matched, Greenhouse Scheduler execution passed, and 113 resources finished at no drift.
- No active GCP/Kubernetes lease or staging relation remains; provider preflight made no mutation; focused validation passes.

## Decisions

- GCP profile execution passes, but its cost objective remains `not_evaluated` until provider charges post.
- PostgreSQL's 192 MiB attempt failed the 80% RSS gate; the approved 256 MiB retry passed without weakening it.
- Azure, AWS, and OCI live gates require interactive credential restoration; none is marked passed.

## Remaining

- Run the remaining PostgreSQL benchmark classes and Kubernetes scale/soak gates.
- Run exact-RC22 Azure, other warehouse/launcher scale, pairwise, and final audit gates within the USD 10 ceiling.
- Restore interactive Azure/AWS/OCI credentials before their live profile and pairwise gates.
- Complete the retained soak through 2026-09-01 before public support release.

## Review First

- `docs/evidence/phase8/2026-08-14/gcp-native-profile.json`
- `docs/evidence/phase8/2026-08-14/kubernetes-lifecycle.json`
- `docs/evidence/phase8/2026-08-14/postgresql-bounded-memory.json`
