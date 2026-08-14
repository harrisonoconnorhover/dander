# Morning Handoff

## Finished

- Applied the reviewed 18-resource disposable GCP Control-plane plan in isolated remote state.
- Kept the graph bucket private, versioned, and configured with zero soft-delete retention.
- Corrected the live verifier for the real optional v1 template name and operator key inventory.
- Verified hosted readiness, current exclusive revisions, numeric config versions, and fail-closed API access.
- Recorded Cloud Run's zero service-scaling defaults without changing scale-to-zero behavior.

## Try It

Run the `verify` action against the protected local input and rendered directory. Do not use or
commit the example file, saved plans, Terraform state, tokens, or graph rows.

## Checks

- Exact-main CI run 31839222261 passed all five jobs at `a501c676`.
- Verifier PR CI run 31842009396 passed all five jobs.
- The corrected read-only verifier passed against the active live deployment.
- A read-only probe plan reduced the normalization diff to the earlier manual CLI metadata only.

## Decisions

- Keep bootstrap permissions narrow; use the authenticated operator only for read-only key inventory.
- Use hosted `/readyz`; Cloud Run owns the externally visible `/healthz` behavior for this profile.
- Preserve revision correlation and record provider-returned zero scaling defaults so checks stay literal.

## Remaining

- Merge the focused scaling-default correction and verify exact-main CI.
- Run the browser graph, restart, no-change, rollback/restore, cleanup, and retained no-drift proofs.

## Review First

- `infra/gcp-control/main.tf`
- `infra/gcp-control/tests/gcp_control.tftest.hcl`
- `tickets/DANDER-130-gcp-control-plane-deployment.md`
