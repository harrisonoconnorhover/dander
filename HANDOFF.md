# Morning Handoff

## Finished

- Added the provider-neutral D6 projection into a separate GCP Cloud Run Terraform profile.
- Added deterministic active/rollback rendering, backend-free preflight, and read-only verification.
- Kept Control and Druff on distinct keyless identities with Control-only GCS object access.
- Disabled soft-delete retention only for the disposable versioned graph bucket.
- Bound verification to the exact reconciled serving revision and mounted config versions.

## Try It

Copy `infra/gcp-control/gcp-control-plane.example.json`, replace its example coordinates and
digests, then run the module's `render` and `preflight` actions. Do not apply from the example.

## Checks

- Full Ruff, mypy, contract drift, and pytest passed: 1,698 passed and 28 skipped.
- Terraform 1.15.8 initialization, validation, and one native Terraform test passed.
- The generated Caddy configuration validated against the exact Druff image.
- The rc22 wheel and source distribution built; the wheel contains the module and complete root.

## Decisions

- Keep this profile in isolated attempt-specific remote state; do not alter retained GCP resources.
- Use numeric startup-config versions mounted outside Druff's immutable application export.
- Require one current ready revision with 100% traffic before accepting provider evidence.

## Remaining

- Commit, open the focused protected PR, merge it, and verify exact-main CI.
- Promote exact images, run bounded live qualification, clean up, and prove retained-GCP no-drift.

## Review First

- `src/dander/deployment/gcp_control_plane.py`
- `infra/gcp-control/main.tf`
- `tickets/DANDER-130-gcp-control-plane-deployment.md`
