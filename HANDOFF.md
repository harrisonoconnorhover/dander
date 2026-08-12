# Morning Handoff

## Finished

- Published exact protected-main candidate `dander-platform==0.9.0rc3` and verified its public wheel.
- Completed OCI stage zero and migrated its state into the private versioned Object Storage bucket.
- Published the source-free RC3 runtime index in the disposable GCP proof registry.
- Captured the live OCI KMS requirement for an explicit automatic-rotation schedule.
- Captured OCI's live list-summary versus exact-repository metadata behavior.

## Try It

Run the focused OCI image-promotion and controller-publication tests.

## Checks

- Protected-main CI passed for the exact `v0.9.0rc3` candidate.
- Live OCI stage-zero retry reached zero drift after one private repository create.
- The rotation-schedule correction passed PR CI; protected-main CI is running.
- Live promotion failed closed before copying because OCI's list summary omitted immutability.
- Focused tests and full protected CI remain required for the metadata correction.

## Decisions

- Use an explicit 365-day OCI KMS automatic-rotation interval.
- Verify exact OCIR metadata with a repository get after resolving one named list summary.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge the exact-repository verification correction through protected CI.
- Retry the one-resource foundation plan after the combined protected-main CI passes.
- Resume digest-preserving OCIR promotion from the accepted GCP runtime index.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/bootstrap/oci_image.py`
- `tests/bootstrap/test_oci_image_promotion.py`
- `tests/bootstrap/test_oci_controller_publication.py`
