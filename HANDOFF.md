# Morning Handoff

## Finished

- Recorded immutable `dander-platform==0.9.0rc20` publication at exact protected-main commit
  `75c5654e95439eaf18e90fbacc849799f4fe42b6` and tag `v0.9.0rc20`.
- Recorded trusted-publishing run `31815063258`, exact public hashes, and PyPI-only verification.
- Reconciled D6/D7 package availability without claiming a current container image or live proof.
- Preserved RC19 contract-bundle history and all local/provider promotion boundaries.

## Try It

Install `dander-platform==0.9.0rc20` from PyPI; container qualification remains a separate gate.

## Checks

- Exact-main CI run `31814445508` passed all five jobs at the tagged commit.
- Trusted-publishing run `31815063258` passed; PyPI hashes and artifact sizes matched exactly.
- Fresh no-cache PyPI-only CLI, scaffold, project, import-origin, and Terraform checks passed.
- Documentation links, stale boundaries, whitespace, and secret/artifact scope review pass.

## Decisions

- RC20 is the first immutable package containing D6/D7, but is not a container-image publication.
- RC19 remains the immutable historical Control-bundle consumer boundary.
- Local live qualification still waits for exact reviewed Dander and Druff image digests.

## Remaining

- Complete independent completion review and protected PR/exact-main CI for this evidence.
- Publish reviewed exact Dander and Druff candidate images without support promotion.
- Run D7 local HTTPS/OIDC/restart/no-drift/rollback/cleanup qualification.

## Review First

- `docs/control-contracts.md`
- `tickets/DANDER-128-local-control-plane-deployment.md`
- `docs/release-audit.md`
