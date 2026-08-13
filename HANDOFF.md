# Morning Handoff

## Finished

- Published `dander-platform==0.9.0rc18` from protected main through the approved trusted PyPI
  workflow and created the immutable `v0.9.0rc18` tag and matching GitHub beta prerelease.
- Verified the public wheel in a fresh environment outside every checkout.
- Recorded the exact tag, commit, workflow, distribution hashes, contract digest, and verification
  result in the release evidence ledger.
- Completed DANDER-119 without changing the already-published package.

## Try It

Install `dander-platform==0.9.0rc18` from PyPI. The installed manifest reports
`io.dander.control.contracts/v1` at digest
`344ef5ff2d685d5bedf7a1ddb119a42a6de08d90f285dc0a981e79c55452c1ed`.

## Checks

- Promotion PR #254 and protected-main CI run `31718983212` passed all five jobs.
- Trusted-publishing run `31719571923` passed build validation and PyPI publication.
- Fresh PyPI install reported `dander 0.9.0rc18`; `dander new` and `dander validate` passed.
- The generated project's Terraform configuration initialized and validated successfully.
- All 25 installed contract files matched their manifest hashes and reviewed bundle digest.

## Decisions

- `0.9.0rc18` is now the only approved immutable source for Druff D1 contract generation.
- Historical RC17 Phase 7 evidence remains fixed to the artifact that produced it.
- The public release changes no provider behavior or retained infrastructure.

## Remaining

- Merge this release-evidence reconciliation through protected CI.
- Generate the Druff consumer from the verified public artifact.
- Prove representative graph parsing, semantic round-trip, compatibility rejection, and CI drift.
- Complete the remaining D1 exit gate before beginning D2.

## Review First

- `docs/control-contracts.md`
- `tickets/DANDER-119-control-contract-bundle.md`
- `docs/release-audit.md`
