# Morning Handoff

## Finished

- Added a hand-rolled `netsuite_suiteql` customer source using the existing OAuth1 TBA, retry, raw-schema, watermark, and SCD1 runtime.
- Added a stateful six-operation FastAPI simulator with invented customers, real HMAC-SHA256 signature verification, offset paging, and named auth/throttle/permission/malformed failures.
- Proved stateful create, update, duplicate-free replay, monotonic watermarks, transport-link removal, and proof-fixture cleanup over loopback HTTP.
- Added the tracked OpenAPI contract, customer staging model, and explicit “simulator-validated, not NetSuite-validated” documentation.
- Preserved completed Odoo work unchanged on `codex/odoo-json2` at `14244cd`.

## Try It

```bash
uv sync --extra dev
uv run python -m dander.dev.netsuite_simulator
uv run pytest tests/integration/test_netsuite_simulator.py
```

## Checks

- Ruff lint and format passed; strict mypy passed for all 150 source files.
- All 664 tests passed.
- Wheel/sdist build and inspection passed; NetSuite contract/docs/fixtures are packaged.
- A source-free wheel installed with the documented `dev` extra and exposed all six simulator operations.
- No NetSuite tenant, GCP resource, Terraform state, public package, or remote branch was changed.

## Decisions

- SuiteQL replaces the old record-list example because the latter returns only IDs and links; the query is uniquely ordered by customer ID.
- The first slice is a full read with bounded pages and idempotent SCD1 replay; 100,000-row and concurrent-offset limits remain explicit.
- OAuth1 TBA is compatibility coverage only. Current OAuth2 and one real-tenant proof gate any supported future release.

## Remaining

- Push `codex/netsuite-simulator` and open a focused PR only when requested.
- Let protected CI repeat Linux tests, packaging, scans, and Terraform validation.
- Obtain an authorized NetSuite/SDN sandbox for the narrow acceptance in `docs/netsuite-simulator.md`.
- Keep the connector out of the supported `0.2.0` surface; consider `0.3.0` only after tenant acceptance.

## Review First

- `src/dander/ingestion/enterprise.py`
- `src/dander/dev/netsuite_simulator.py`
- `tests/integration/test_netsuite_simulator.py`
