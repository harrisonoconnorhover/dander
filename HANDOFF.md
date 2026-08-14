# Morning Handoff

## Finished

- Prepared `dander-platform==0.9.0rc20` from exact protected-main D6/D7 source.
- Recorded the provider-neutral Control service projection and typed GraphStore startup seam.
- Recorded the deterministic digest-only local hosted Compose profile and verifier.
- Kept every current-public install, status, upgrade, audit, and evidence reference on RC19.

## Try It

Run `uv run python scripts/check_release_metadata.py`; publication mode intentionally fails until
the separate RC20 promotion updates current-public references.

## Checks

- Release metadata and focused release tests pass.
- Package build and distribution validation pass.
- Ruff lint/format and Git whitespace validation pass.
- Publication-mode metadata validation fails closed on RC20 versus public RC19, as required.

## Decisions

- RC20 describes the complete exact-main D6/D7 source rather than claiming live deployment proof.
- Local and cloud support remain unpromoted until their separate qualification gates pass.
- Public references remain on RC19 until the separate promotion and publication sequence.

## Remaining

- Complete independent completion review and protected PR CI for this preparation.
- Promote and publish RC20 only after the prepared release commit passes exact-main CI.
- Publish reviewed candidate images, then complete the D7 local live qualification.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `tests/test_release_metadata.py`
