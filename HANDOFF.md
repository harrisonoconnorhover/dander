# Morning Handoff

## Finished

- Published private source-free `0.9.0rc28` from exact protected main `7135b8c` in one attempt.
- Recorded immutable amd64/arm64 index `sha256:f8259276…f94959e` and its four manifests.
- Passed exact-wheel, rootless read-only conformance, and stable entrypoint probes on both arches.
- Passed GCP, Kubernetes, AWS, and Azure selectors without provider access.
- Recorded sanitized candidate evidence without changing public RC20 or support status.

## Try It

Review `docs/evidence/phase8/2026-08-16/rc28-candidate.json` and run the immutable image with
`--version` after authenticating Docker to the private GAR repository.

## Checks

- Preparation PR #351 merged; exact-main run `31961210116` passed all five jobs before publication.
- Wheel and source distribution hashes match the inspected protected-main artifacts.
- Both runnable manifests report `0.9.0rc28`; rootless/read-only probes passed on amd64 and arm64.
- SPDX 2.3 SBOM and SLSA provenance exist for both architectures.
- Registry digest, source-free context, external selectors, JSON, and evidence claims passed review.

## Decisions

- Preserve accepted RC27 results; rerun only materially affected lanes and the final closure matrix.
- Treat RC28 as private candidate evidence, not live qualification, cost, public release, or support.
- Require a fresh protected RC28-bound Azure objective before any Azure mutation.

## Remaining

- Protect this publication record and pass its exact-main CI.
- Bind the Azure qualification objective in a fresh branch from that protected main.
- Keep provider-measured cost pending until invoices post.
- Complete remaining provider/profile, scale, soak, and final-candidate gates.

## Review First

- `docs/evidence/phase8/2026-08-16/rc28-candidate.json`
- `tickets/DANDER-210-private-rc28-candidate.md`
- `docs/cloud-portability-phase8-qualification.md`
