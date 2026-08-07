# Morning Handoff

## Finished

- Synchronized Josh's `upstream/main` onto Harrison's current `origin/main` without dropping either tip.
- Preserved Josh's capability contracts, ticket backlog, and reviewed secret-scan exception.
- Preserved Dander `0.6.0rc1`, its Salesforce CRM overlay, and the accepted release work on main.
- Kept connector capability additions additive to the existing source and plugin contracts.

## Try It

Run `uv run dander validate`, then inspect a configured connector with
`uv run dander connector inspect PIPELINE`.

## Checks

- Ruff lint/format and strict mypy passed.
- Full test suite passed: `770 passed`.
- Locked production dependency audit found no known vulnerabilities.
- Platform and stage-zero Terraform format/init/validation passed with backends disabled.
- Wheel/sdist inspection and a clean external source-free install/validation passed.
- The Linux container built and passed CLI, unprivileged-user, and bundled-asset checks.

## Decisions

- Use a merge commit so both repositories' existing commit identities remain visible.
- Keep `0.6.0rc1` as the package version from Harrison's newer release tip.
- Treat this PR as synchronization only; no release, deployment, or schedule mutation is included.

## Remaining

- Merge this synchronization PR after review and protected CI.
- Rebase or retarget Dander PRs #87 and #88 onto synchronized `main`, preserving their stack order.
- Publication, live acceptance, retained-project changes, and schedules remain separately gated.

## Review First

- `src/dander/ingestion/capabilities.py`
- `docs/decisions.md`
- `.gitleaksignore`
