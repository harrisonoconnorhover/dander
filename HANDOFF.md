# Morning Handoff

## Finished

- Merged protected PR #215 for the OCI lifecycle controller; protected-main CI is green.
- Added digest-preserving OCIR runtime promotion with no provider-specific rebuild.
- Derived a repository-scoped registry token from the expiring OCI SecurityToken session.
- Kept the token in a mode-0600 temporary Docker config while preserving source-registry helpers.
- Added fail-closed repository, index, platform-map, idempotency, cleanup, and CLI contracts.

## Try It

Run `uv run pytest tests/bootstrap/test_oci_image_promotion.py`.

## Checks

- Focused OCIR promotion and OCI CLI tests pass.
- Focused Ruff lint/format and Mypy pass.
- Protected-main CI at `e79e30be67cc9abd1df14dfb941a0046c8bacc50` passes.

## Decisions

- Runtime indexes are copied, never rebuilt, and must retain index plus platform digests.
- OCIR user auth tokens and registry passwords remain prohibited; use scoped ephemeral access tokens.
- The reviewed stage-zero repository must already be private, immutable, and available.

## Remaining

- Merge the focused OCIR promotion implementation and verify protected-main CI.
- Add protected controller-image publication from an exact reviewed wheel.
- Obtain explicit approval for public candidate publication and a numeric OCI per-attempt ceiling.
- Run read-only OCI preflight, reviewed plans, live profile/rotation/rollback/cleanup, and no drift.
- Merge sanitized live evidence, then make the binary Phase 7 exit-gate recommendation.

## Review First

- `src/dander/bootstrap/oci_image.py`
- `tests/bootstrap/test_oci_image_promotion.py`
- `src/dander/cli/oci_command.py`
