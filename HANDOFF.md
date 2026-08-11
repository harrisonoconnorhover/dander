# Morning Handoff

## Finished

- Corrected the Phase 6 live-proof order so stage zero supplies the real managed-identity client ID
  before the exact source-free candidate is built.
- Preserved the requirement that cost, publication, provider-registration, apply, secret-write, and
  execution approval precede every cloud write.
- Kept candidate publication ahead of the platform plan, platform apply, and every job execution.

## Try It

Review the ordered proof in `docs/cloud-portability-azure-lifecycle-acceptance.md`.

## Checks

- Protected main at `eeaccf36` is fully green before this documentation-only correction.
- The correction changes no runtime, Terraform, provider, or credential behavior.
- No provider registration, resource creation, secret write, image publication, or paid operation ran.

## Decisions

- Azure assigns the user-managed identity client ID, so the exact candidate follows stage zero.
- The accepted candidate remains mandatory before platform planning or execution.
- Repeat attempts use the same approved per-attempt ceilings and cleanup boundary.

## Remaining

- Merge this ordering correction through protected CI.
- Register the approved Azure providers and review/apply stage zero.
- Publish and copy the exact protected-main candidate under the approved ceilings.
- Run the approved live lifecycle, federation, rotation, rollback, cleanup, and no-drift proof.
- Perform the final independent completion review and Phase 6 gate reassessment.

## Review First

- `docs/cloud-portability-azure-lifecycle-acceptance.md`
- `docs/decisions.md`
