# Morning Handoff

## Finished

- Corrected the D7 named-volume initializer after its first live Docker start failed closed.
- Granted only `FOWNER` in addition to `CHOWN` for the fixed owner/mode command.
- Kept it networkless, read-only, no-new-privileges, and limited to the named volume.
- Rebased over protected Phase 8 merge `fe325ff` without changing its implementation.

## Try It

Render the D7 local profile and start Compose with an empty named volume; initialization must exit zero.

## Checks

- The pre-change start failed exactly at `chmod 0700`; the empty stack was removed completely.
- The corrected initializer exited zero and the active live verifier passed all ten checks.
- Six focused and the full test suite passed; full Ruff lint/format passed.
- Focused mypy passed; protected main `fe325ff` CI passed all five jobs before the rebase.
- Full macOS mypy retained four known platform-only unused-ignore findings.

## Decisions

- `CHOWN` changes the volume owner; `FOWNER` is separately required to change its mode.
- Adding exactly `FOWNER` is smaller than weakening the accepted `0700` mode.
- The incomplete live attempt is diagnostic evidence, not a passed D7 qualification.

## Remaining

- Merge this focused protected correction and verify exact-main CI.
- Resume D7 HTTPS OIDC, persistence, no-drift, rollback, restore, and cleanup.
- Continue the separately merged Phase 8 work without coupling it to D7.

## Review First

- `infra/local/compose.yaml`
- `src/dander/deployment/local_compose.py`
- `tests/deployment/test_local_compose.py`
