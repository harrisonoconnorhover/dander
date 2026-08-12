# Morning Handoff

## Finished

- Confirmed live OCI account activation and isolated the Phase 7 compartment.
- Captured Oracle's live rejection of repository-level tag immutability on create and update.
- Adapted OCIR bootstrap/publication to private, verified digest addressing without false parity.

## Try It

Run `uv run pytest -q` against the OCI Terraform, image-promotion, and controller-publication tests.

## Checks

- Live OCI SecurityToken, Object Storage, and Container Instances preflight pass.
- Stage zero created only its private versioned bucket before OCIR rejected `isImmutable`.
- All 106 focused OCI/distribution/runtime tests, Ruff, Mypy, Terraform validation, and packaged
  bootstrap inspection pass; full protected CI remains to run.

## Decisions

- Treat OCIR tag immutability as an explicit unavailable capability in this tenancy.
- Preserve artifact immutability through verified digests and matching Function tag/digest inputs.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge the correction through protected CI and publish a new exact release candidate.
- Resume the approved live OCI profile from the preserved partial stage-zero state.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `infra/oci/bootstrap-admin/main.tf`
- `src/dander/bootstrap/oci_image.py`
- `docs/decisions.md`
