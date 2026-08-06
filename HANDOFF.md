# Morning Handoff

## Finished

- Synchronized Josh's accepted capability work into Harrison's protected `main` through PR `#90`.
- Added explicit JSONL invocation for optional `get_deleted` feeds in PR `#87`.
- Added confirmed create/update/upsert/delete invocation in stacked PR `#88`.
- Kept provider writes, scheduled execution, BigQuery deletion, and deployment out of core.
- Used one stateful fake to prove write dispatch and repeat-safe delete outcomes.

## Try It

Run `uv run dander connector get-deleted SOURCE ENDPOINT --since CURSOR` for a supported feed.
Write-back uses `uv run dander connector write SOURCE ENDPOINT OPERATION` with operation-specific
JSON files and the required `--confirm-write` acknowledgement.

## Checks

- Synchronized `main` passed all five protected CI checks.
- Pre-synchronization capability suite, Ruff, formatting, and strict mypy passed.
- The synchronized #87 branch passed its full local suite: `772 passed`.
- The synchronized #88 full local suite passed: `774 passed`.

## Decisions

- Keep read and mutation entry points explicit rather than adding them to normal pipeline runs.
- Require file-based JSON and confirmation before loading a source for write-back.
- Make provider plugins own retry and idempotency behavior; core does not retry capability calls.

## Remaining

- Merge PR `#87` through protected `main`, then retarget and merge stacked PR `#88`.
- Update the Salesforce provider stack to the published Dander contract version.
- Publish candidates and run live acceptance only after the complete protected stack passes.

## Review First

- `src/dander/cli/main.py`
- `tests/cli/test_connector_cli.py`
- `src/dander/ingestion/capabilities.py`
