# Morning Handoff

## Finished

- Applied the reviewed 18-resource disposable GCP Control-plane plan in isolated remote state.
- Kept the graph bucket private, versioned, and configured with zero soft-delete retention.
- Corrected the live verifier for the real optional v1 template name and operator key inventory.
- Verified hosted readiness, current exclusive revisions, numeric config versions, and fail-closed API access.
- Left graph data untouched while correcting provider-boundary assumptions.

## Try It

Run the `verify` action against the protected local input and rendered directory. Do not use or
commit the example file, saved plans, Terraform state, tokens, or graph rows.

## Checks

- Exact-main CI run 31839222261 passed all five jobs at `a501c676`.
- Focused GCP tests passed 12 tests; Ruff and focused mypy passed.
- The corrected read-only verifier passed against the active live deployment.
- The saved apply plan created exactly 18 resources with no retained-stack changes.

## Decisions

- Keep bootstrap permissions narrow; use the authenticated operator only for read-only key inventory.
- Use hosted `/readyz`; Cloud Run owns the externally visible `/healthz` behavior for this profile.
- Preserve status generation/revision/traffic correlation when the v1 desired template omits a name.

## Remaining

- Merge this focused verifier correction and verify exact-main CI.
- Run the browser graph, restart, no-change, rollback/restore, cleanup, and retained no-drift proofs.

## Review First

- `src/dander/deployment/gcp_control_plane.py`
- `tests/deployment/test_gcp_control_plane.py`
- `tickets/DANDER-130-gcp-control-plane-deployment.md`
