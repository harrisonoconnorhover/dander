# Morning Handoff

## Finished

- Reproduced the live Azure Blob list-revision mismatch from protected-main source.
- Canonicalized quoted and unquoted Azure SDK ETag shapes to one opaque quoted revision.
- Added a focused regression using the provider's real create/get-versus-list representation split.
- Kept DANDER-124 open until the corrected protected-main source passes the live proof.

## Try It

Run `uv run pytest -q tests/control/test_azure_blob_graph_store.py`.

## Checks

- The pre-change live diagnostic proved quote stripping was the only revision difference.
- Sixty-seven focused Azure/shared GraphStore tests and the full pytest suite passed.
- Ruff, full strict typing across 414 source files, contract drift, and `git diff --check` passed.
- Independent adversarial completion review passed with no material finding.

## Decisions

- Normalize only Azure's two equivalent SDK ETag shapes; preserve the provider-neutral revision API.
- Make no generic revision refactor and no Azure deployment or support-status change.

## Remaining

- Complete protected CI and merge the focused correction.
- Rerun the live proof from exact protected main and clean Azure exactly.
- Record coordinate-free evidence only after the corrected live attempt passes.

## Review First

- `src/dander/control/azure_blob_graph_store.py`
- `tests/control/test_azure_blob_graph_store.py`
- `tests/control/azure_blob_fakes.py`
