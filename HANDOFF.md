# Morning Handoff

## Finished

- Public `0.9.0rc17` passed the named OCI/PostgreSQL lifecycle on one digest-preserved GAR/OCIR index.
- Proved scheduling, replay, fencing, cancellation, retry exhaustion, Vault rotation, and bounded logs.
- Proved immutable RC16 rollback and RC17 restoration, then removed every live Container Instance.
- Verified both OCI Terraform roots and both retained-GCP roots at exact no drift.
- Recorded the Phase 7 acceptance, provider limitations, ticket completion, and fail-closed OCI-to-Google boundary.

## Try It

Run `uv run python scripts/check_release_metadata.py` and `uv run pytest -q tests/test_release_metadata.py`.

## Checks

- Protected-main CI run `31697938533` — passed all required jobs.
- Public release workflow `31698916153` — passed; wheel and source distribution published.
- RC17 live profile — 17 rows, one model, three assertions; passed.
- OCI stage-zero/foundation and retained-GCP stage-zero/platform plans — exact no changes.
- Final OCI inventory — zero non-deleted Container Instances; schedule inactive.

## Decisions

- Keep OCI-to-Google unsupported; do not substitute static Google keys for missing federation.
- Keep OCIR tag immutability and default-Vault master-key rotation limitations explicit.
- Phase 7 passes, but OCI remains experimental until Phase 8 qualification.

## Remaining

- Merge the focused Phase 7 evidence PR after protected CI and completion review.
- Execute the approved Phase 8 benchmark and pairwise-profile matrix under explicit ceilings.
- Complete current-profile release-candidate soak and operations documentation.
- Freeze the tested support matrix only after all Phase 8 gates pass.

## Review First

- `docs/cloud-portability-oci-lifecycle-acceptance.md`
- `docs/evidence/oci/2026-08-13/phase7.json`
- `docs/cloud-portability-plan.md`
