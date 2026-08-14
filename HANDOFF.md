# Morning Handoff

## Finished

- Inherited DRUFF D7 local qualification from merged PR #278 at protected main `69b17a0` without changing its implementation or evidence.
- Merged the runtime-inspection selector and DANDER-201 retained evidence through PR #276 at protected main `011ee6e`.
- Prepared private `0.9.0rc22` metadata on top of the new main; public RC20 remains unchanged.
- Classified RC21 as diagnostic-only after its combined bundle exposed the missing selector.
- Recorded RC22-specific allocation within the unchanged USD 10 ceiling and preserved the separate Druff boundary.

## Try It

Run `uv run python scripts/check_release_metadata.py`; publication mode must still reject private RC22 against public RC20.

## Checks

- PR #276 review passed with no threads; exact-head and exact-main CI each passed all five jobs.
- DRUFF's active, rollback, and restored-active verifiers passed all ten runtime checks; cleanup evidence is retained in its merged report.
- RC21 wheel/sdist, source-free bundle, multi-platform image, SBOM, and provenance passed before its selector gate failed closed.
- Retained GCP apply changed five images with zero add/destroy; sanitized diagnostics and post-apply no drift passed.
- RC22 metadata, release tests, wheel/sdist distribution validation, Ruff, publication guard, authorization JSON, and allocation sum pass locally.

## Decisions

- RC22 supersedes diagnostic-only RC21 as the private qualification candidate; do not weaken the one-digest or source-free gates.
- Provider-measured charges have not posted; no exact spend is claimed.
- Kubernetes lifecycle-adapter implementation remains excluded because it overlaps Druff.

## Remaining

- Re-review and merge rebased RC22, then record its exact merge-bound publication manifest and privately publish the source-free image.
- Run exact-RC22 Kubernetes, GCP, Azure, scale, pairwise, and final audit gates within the USD 10 ceiling.
- Record unavailable AWS/OCI credentials as provider blockers without weakening gates.
- Complete the retained soak through 2026-09-01 before public support release.

## Review First

- `CHANGELOG.md`
- `docs/evidence/phase8/2026-08-14/rc22-authorization.json`
- `docs/evidence/local/2026-08-14/d7-control-plane.json`
