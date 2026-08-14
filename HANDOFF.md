# Morning Handoff

## Finished

- Recorded immutable `dander-platform==0.9.0rc19` publication evidence at exact protected-main
  commit `cad383b8ac74e8ba0ce0b3b92c66b0a5a93a306b` and tag `v0.9.0rc19`.
- Recorded the trusted PyPI workflow, artifact hashes, and complete Control bundle digest.
- Replaced the stale unpublished DANDER-121 consumer boundary with the verified RC19 boundary.
- Preserved honest provider status: only GCS is live-qualified; S3, Azure, and OCI are unpromoted.

## Try It

Install `dander-platform==0.9.0rc19` from PyPI; Druff may generate only from that release artifact.

## Checks

- Exact-main CI run `31784964851` passed all five jobs at the tagged commit.
- Trusted-publishing run `31785512985` passed and PyPI hashes matched the public artifacts.
- Fresh PyPI-only CLI, scaffold, project validation, and Terraform validation passed.
- All 37 installed contract files matched the manifest and bundle digest `695791df...a12be3`.
- Documentation diff, stale-reference scan, and secret/artifact review passed.

## Decisions

- Preserve DANDER-119 as the immutable RC18 record; DANDER-121 owns the superseded source claim.
- RC19 is an immutable prerelease artifact, not a support-status promotion.
- Druff must consume RC19 from PyPI and must not read a sibling Dander checkout.

## Remaining

- Merge this focused evidence PR and verify exact-main CI.
- Refresh Druff's generated contracts and pins from public RC19.
- Complete DRUFF-25 through DRUFF-29 in focused protected PRs.

## Review First

- `docs/control-contracts.md`
- `tickets/DANDER-121-hosted-control-api.md`
- `docs/release-audit.md`
