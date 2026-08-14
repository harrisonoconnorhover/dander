# Morning Handoff

## Finished

- Inherited DRUFF's hosted-Control implementation through merged PR #279 at protected main `95540e8` without changing its chart or runtime.
- Merged the runtime-inspection selector, DANDER-201 evidence, and private RC22 preparation; exact-main CI passed all five jobs.
- Published source-free private RC22 index `sha256:ce395d…47c3` with amd64/arm64 manifests, SBOM, and provenance.
- Passed read-only registry-image version and selected GCP/Kubernetes runtime inspections.
- Recorded the exact candidate plus pre-mutation Kubernetes correctness and failure objective sets within the unchanged USD 10 ceiling.

## Try It

Inspect `docs/evidence/phase8/2026-08-14/rc22-candidate.json`; public RC20 remains unchanged.

## Checks

- PR #277 review passed with no threads; exact-head and exact-main CI each passed all five jobs.
- DRUFF's full pytest, focused typing, Ruff, contract-drift, Helm, and distribution checks are retained from its merged lane.
- RC22 wheel/sdist, clean install, source-free bundle, multi-platform image, SBOM, provenance, and registry digest checks pass.
- Retained GCP apply changed five images with zero add/destroy; sanitized diagnostics and post-apply no drift passed.
- Candidate and objective evidence parse; allocation remains USD 10 and provider invoice data is pending.

## Decisions

- RC22 is the private qualification candidate; RC21 remains diagnostic-only and public RC20 remains current.
- Provider-measured charges have not posted; no exact spend is claimed.
- Keep Phase 8 qualification separate from DRUFF's hosted-Control implementation and qualification.

## Remaining

- Record exact-RC22 Kubernetes lifecycle evidence, then run its scale and soak gates.
- Run exact-RC22 GCP, Azure, scale, pairwise, and final audit gates within the USD 10 ceiling.
- Record unavailable AWS/OCI credentials as provider blockers without weakening gates.
- Complete the retained soak through 2026-09-01 before public support release.

## Review First

- `docs/evidence/phase8/2026-08-14/rc22-candidate.json`
- `docs/evidence/phase8/2026-08-14/kubernetes-correctness-objectives.json`
- `docs/cloud-portability-phase8-qualification.md`
