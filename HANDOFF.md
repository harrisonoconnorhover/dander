# Morning Handoff

## Finished

- Merged protected PR #219 for OCI digest identity without false tag-immutability parity.
- Verified full protected-main CI at `1cc7b1e47aaf1550ec299944c040343d3491643f`.
- Prepared Dander `0.9.0rc3` release metadata from that protected baseline.

## Try It

Run `python3 scripts/check_release_metadata.py`, then build and inspect the distribution.

## Checks

- Protected-main Python, Terraform, secret, distribution, container, and vulnerability checks pass.
- Release metadata, lock, focused tests, Ruff, Mypy, wheel/sdist inspection, and packaged OCI
  bootstrap checks pass for `0.9.0rc3`; protected PR CI remains required.

## Decisions

- Publish only from exact tag `v0.9.0rc3` on the next green protected-main merge commit.
- Keep the accepted public wheel and its digest outside the repository for live OCI proof.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge the version-only PR through protected CI, then tag and publish `0.9.0rc3`.
- Resume the approved live OCI profile from the preserved partial stage-zero state with that wheel.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `pyproject.toml`
- `CHANGELOG.md`
- `scripts/check_release_metadata.py`
