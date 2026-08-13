# Morning Handoff

## Finished

- Prepared public `dander-platform==0.9.0rc13` from protected main.
- Published and deployed exact RC12 runtime/controller artifacts from protected main.
- Recovered an expired-token Terraform apply without infrastructure or state loss.
- Deployed the A1 projection and Function notification permission at verified no drift.
- Isolated the live A1 create rejection to OCI's parent/child tag-equality contract.

## Try It

Run `uv run pytest -q tests/providers/test_oci_container_instances_adapter.py`.

## Checks

- RC12 protected-main CI, PyPI publication, cross-cloud promotion, and no-drift verification passed.
- The diagnostic A1 instance was immediately deletion-requested; the managed run created none.
- Focused OCI lifecycle tests passed (20); Ruff and strict typing passed.

## Decisions

- Preserve identical parent and child OCI free-form tags because the live API requires parity.
- Preserve the schedule as inactive until the controlled scheduled-run proof.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc13` through protected CI.
- Promote and deploy the corrected controller, then prove a successful A1 run.
- Complete success, rotation, overlap, retry, replay, cancel, schedule, and rollback proofs.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/providers/oci_container_instances/oci_adapter.py`
- `tests/providers/test_oci_container_instances_adapter.py`
- `HANDOFF.md`
