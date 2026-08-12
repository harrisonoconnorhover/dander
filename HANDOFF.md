# Morning Handoff

## Finished

- Merged protected PR #217 for exact-wheel OCI controller publication.
- Prepared Dander `0.9.0rc2` from protected main without publishing it.
- Reconciled exact release-version references and added Phase 7 candidate notes.

## Try It

Run `python3 scripts/check_release_metadata.py`, then build and inspect the distribution.

## Checks

- Protected-main CI at `653e93f498b3b702daefc8872d48f9c589a31e46` passes.
- Release metadata, lock, wheel/sdist, exact-wheel, Ruff, Mypy, and focused tests pass locally.
- Full protected CI remains required before merge and again on protected main before tagging.

## Decisions

- Publish only from an exact `v0.9.0rc2` tag on the green protected-main merge commit.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 support qualification pass.
- Do not tag, publish, or create paid OCI resources without explicit approvals.

## Remaining

- Merge this version-only PR through protected CI after publication approval.
- Tag and publish `0.9.0rc2` through the reviewed `pypi` environment.
- Run the approved live OCI profile, rotation, rollback, cleanup, and no-drift proof.
- Merge sanitized evidence and make the binary Phase 7 exit-gate recommendation.

## Review First

- `pyproject.toml`
- `CHANGELOG.md`
- `scripts/check_release_metadata.py`
