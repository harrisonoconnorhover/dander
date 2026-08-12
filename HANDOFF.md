# Morning Handoff

## Finished

- Merged protected PR #216 for digest-preserving OCIR runtime promotion.
- Added OCI controller publication bound to one exact reviewed wheel SHA-256.
- Build inputs now come only from the wheel through an ephemeral source-free context.
- Added deterministic immutable tagging, amd64 verification, and sanitized artifact recording.
- Added fail-closed rerun, scoped-token, confirmation, and cleanup contracts.

## Try It

Run `uv run pytest tests/bootstrap/test_oci_controller_publication.py tests/cli/test_oci_cli.py`.

## Checks

- Focused controller, OCIR promotion, and OCI CLI tests pass.
- Focused Ruff lint/format and Mypy pass.
- Protected-main CI at `fae47a3cf860ba74a7c40b63c84ca21b9db7c6a2` passes.

## Decisions

- The controller is a wheel-built amd64 Function image, separate from the copied task runtime.
- A dirty checkout cannot contribute files to the controller build context.
- Existing controller tags require an exact local wheel/tag/digest binding.

## Remaining

- Merge the protected controller-publication PR and verify protected-main CI.
- Prepare the Phase 7 release candidate without publishing it.
- Obtain explicit approval for public candidate publication and a numeric OCI per-attempt ceiling.
- Run approved live OCI publication, profile, rotation, rollback, cleanup, and no-drift proof.
- Merge sanitized evidence and make the binary Phase 7 exit-gate recommendation.

## Review First

- `src/dander/bootstrap/oci_image.py`
- `tests/bootstrap/test_oci_controller_publication.py`
- `src/dander/cli/oci_command.py`
