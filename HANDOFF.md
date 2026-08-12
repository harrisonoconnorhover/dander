# Morning Handoff

## Finished

- Prepared the exact protected-main `dander-platform==0.9.0rc8` candidate.
- Diagnosed live OCIR Buildx authorization without uploading an artifact.
- Confirmed the existing token scope correctly includes the tenancy namespace.
- Preserved OCI's bearer token in Docker's native `registrytoken` field.
- Kept the scoped token out of command arguments and ephemeral after command exit.

## Try It

Run `pytest -q tests/bootstrap/test_oci_image_promotion.py tests/bootstrap/test_oci_controller_publication.py`.

## Checks

- Live registry probes returned `403` without the namespace and authenticated `not found` for the
  namespace-qualified token in Docker's `registrytoken` field.
- Focused tests cover both OCIR publication paths and isolated Docker configuration.
- Protected-main CI passed for the scoped-token correction before the RC7 cut.

## Decisions

- Use Docker's native registry-token field for OCI's already-exchanged bearer token.
- Preserve the one-use mode-`0600` Docker configuration and namespace-qualified repository scope.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc8` through protected CI.
- Resume digest-preserving OCIR promotion from that exact accepted runtime index.
- Publish and deploy the exact-wheel OCI lifecycle controller.
- Complete the bounded live lifecycle acceptance matrix.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/bootstrap/oci_image.py`
- `tests/bootstrap/test_oci_image_promotion.py`
- `HANDOFF.md`
