# Morning Handoff

## Finished

- Added explicit canonical JSON-to-Redshift `SUPER` mapping through
  `redshift/fallback=super` without widening canonical schema v1.
- Staged strict deterministic JSON as `VARBYTE(16777216)` and applied `JSON_PARSE` inside every
  fenced ingestion-mode publication.
- Rejected bare JSON, ARRAY/RECORD, invalid extensions, non-finite JSON, non-string object keys, and
  SUPER keys/cursors/snapshot fields before upload.
- Enforced Redshift's 4 MB COPY row limit per normalized row, including multi-row artifacts.
- Allowed explicitly declared native SUPER outputs in fenced Redshift table/incremental models.
- Updated compatibility and experimental-provider documentation without promoting support.

## Try It

Run `uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py tests/test_compatibility.py`.

## Checks

- Ruff formatting and lint passed across 328 files; strict mypy passed across 304 files.
- Full pytest passed: 1,145 tests with 13 intentional skips.
- Wheel and source archive passed distribution inspection, installed outside the checkout, and each
  generated and validated a source-free project pinned to `0.8.0rc8`.

## Decisions

- SUPER requires an exact field extension and is never inferred from bare JSON.
- Binary staging avoids Redshift's stored-VARCHAR limit; strict JSON validation prevents remote-only
  parsing failures and silent key coercion.
- The existing 4 MB staged-row guard remains stricter than Redshift's 16 MB SUPER boundary.

## Remaining

- Push a focused PR, require protected CI, and merge only if clean.
- Keep direct transport, graphs, views, telemetry, and paid live proof in separate slices.

## Review First

- `src/dander/providers/redshift/writer.py`
- `src/dander/providers/redshift/runtime.py`
- `src/dander/providers/redshift/transform.py`
