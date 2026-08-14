# Morning Handoff

## Finished

- Inherited DRUFF's hosted-Control qualification through PR #281 at protected main `1bbddb8` without changing it.
- Published source-free private RC22 index `sha256:ce395d…47c3`; public RC20 remains unchanged.
- Passed exact-RC22 Kubernetes lifecycle plus five normalized launcher-scale classes on kind 1.32.2.
- Passed seven exact-RC22 PostgreSQL classes through transform and provider-specific failure objectives.
- Passed the exact-RC22 retained GCP profile rerun: authenticated manual/replay, Scheduler execution, cleanup, and no drift.

## Try It

Inspect the GCP, Kubernetes, and PostgreSQL reports in `docs/evidence/phase8/2026-08-14/`.

## Checks

- Candidate wheel/sdist, source-free image, multi-platform registry record, SBOM, provenance, and read-only runtime inspections pass.
- Kubernetes scale Jobs passed correctness/bulk/incremental/transform/failure under 2 CPU/512 MiB; clusters were deleted.
- PostgreSQL processed 2.7248 GB under 256 MiB; transform passed scan/join/aggregation/incremental merge and 21 assertions over 100k facts.
- GCP passed 23 deployment checks; Salesforce replay counts matched, Greenhouse Scheduler execution passed, and 113 resources finished at no drift.
- PostgreSQL failure recovery/cancellation passed; no lease, staging relation, schema, container, or network remains.

## Decisions

- GCP profile execution passes, but its cost objective remains `not_evaluated` until provider charges post.
- RC22 PostgreSQL has COPY only, so crossover remains open until a bounded direct transport exists to compare.
- Azure, AWS, and OCI live gates require interactive credential restoration; none is marked passed.

## Remaining

- Resolve PostgreSQL crossover/hosted cost; run remaining Kubernetes classes and soak.
- Run exact-RC22 Azure, other warehouse/launcher scale, pairwise, and final audit gates within the USD 10 ceiling.
- Restore interactive Azure/AWS/OCI credentials before their live profile and pairwise gates.
- Complete the retained soak through 2026-09-01 before public support release.

## Review First

- `docs/evidence/phase8/2026-08-14/kubernetes-postgresql-scale-attempts.json`
- `docs/evidence/phase8/2026-08-14/postgresql-incremental.json`
- `docs/evidence/phase8/2026-08-14/gcp-native-profile.json`
