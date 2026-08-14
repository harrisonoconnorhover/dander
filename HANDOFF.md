# Morning Handoff

## Finished

- Merged the separate GCP Cloud Run profile after complete protected PR and exact-main CI.
- Confirmed the retained GCP state root and target project without changing cloud resources.
- Corrected the bucket verifier to consume the real snake-case `gcloud storage` JSON shape.
- Kept fail-closed checks for uniform access, public-access prevention, versioning, and soft delete.
- Preserved the exact serving-revision and numeric startup-config verification boundaries.

## Try It

Copy `infra/gcp-control/gcp-control-plane.example.json`, replace its example coordinates and
digests, then run the module's `render` and `preflight` actions. Do not apply from the example.

## Checks

- Protected-main CI run 31837962005 passed all five jobs at `54c8581`.
- Full Ruff lint/format, mypy, and Control contract drift checks passed.
- The full pytest suite passed; the focused GCP verifier suite passed 12 tests.
- Read-only `gcloud storage buckets describe` confirmed the provider CLI field shape.

## Decisions

- Keep this profile in isolated attempt-specific remote state; do not alter retained GCP resources.
- Use numeric startup-config versions mounted outside Druff's immutable application export.
- Match the exact CLI schema used by live verification rather than the Storage REST representation.

## Remaining

- Merge the focused verifier correction and verify its exact-main CI.
- Promote exact images, run bounded live qualification, clean up, and prove retained-GCP no-drift.

## Review First

- `src/dander/deployment/gcp_control_plane.py`
- `tests/deployment/test_gcp_control_plane.py`
- `tickets/DANDER-130-gcp-control-plane-deployment.md`
