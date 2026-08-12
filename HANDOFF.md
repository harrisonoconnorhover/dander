# Morning Handoff

## Finished

- Prepared the exact protected-main `dander-platform==0.9.0rc7` candidate.
- Diagnosed live OCIR Buildx authentication without uploading an artifact.
- Confirmed the short-lived token and exact `pull,push` repository scope are valid.
- Changed isolated Docker credentials to OCIR's supported `BEARER_TOKEN` login form.
- Kept the scoped token out of command arguments and ephemeral after command exit.

## Try It

Run `pytest -q tests/bootstrap/test_oci_image_promotion.py tests/bootstrap/test_oci_controller_publication.py`.

## Checks

- Live registry probes returned `403` for `identitytoken` and authenticated `not found` for the
  same token in `BEARER_TOKEN` Basic form.
- Focused tests cover both OCIR publication paths and isolated Docker configuration.
- Protected-main CI passed for the scoped-token correction before the RC7 cut.

## Decisions

- Use Docker's ordinary `auth` field with the fixed OCIR access-token username.
- Preserve the one-use mode-`0600` Docker configuration and exact repository scope.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc7` through protected CI.
- Resume digest-preserving OCIR promotion from the exact accepted runtime index.
- Publish and deploy the exact-wheel OCI lifecycle controller.
- Complete the bounded live lifecycle acceptance matrix.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/bootstrap/oci_image.py`
- `tests/bootstrap/test_oci_image_promotion.py`
- `HANDOFF.md`
