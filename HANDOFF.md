# Morning Handoff

## Finished

- Merged the simulator-validated NetSuite SuiteQL foundation through protected PR #52.
- Added a read-only Odoo 19+ JSON-2 `res.partner` source, connector template, staging model, and operator guide.
- Proved Odoo against ephemeral official Odoo/PostgreSQL containers, then removed them.
- Rebased the Odoo work onto the NetSuite-enabled `main` while preserving both enterprise sources.
- Preserved the original Odoo commit on `backup/odoo-json2-pre-netsuite`.

## Try It

```bash
cp connectors/odoo.example.yaml connectors/odoo.yaml
export ODOO_API_KEY='YOUR_API_KEY'
uv run dander run odoo --dry-run --sandbox --project YOUR_PROJECT
```

## Checks

- Ruff lint/format and strict mypy passed; all 672 tests passed after the rebase.
- NetSuite, Odoo, Workday, and generic dlt source routing pass together through one adapter helper.
- The earlier Odoo live JSON-2 acceptance passed with bounded two-row pages, bearer auth, database routing, null normalization, and watermark replay.
- No Odoo deployment, retained-project change, version bump, or package publication occurred.

## Decisions

- Odoo targets the current JSON-2 API; deprecated XML-RPC/JSON-RPC is not supported.
- The first slice is read-only `res.partner`; no retained-project Odoo pipeline was added.
- Salesforce remains memory-bounded but still rereads Accounts through synchronous QueryAll.

## Remaining

- Push the rebased Odoo branch and open the focused PR.
- Let protected CI repeat Linux tests, packaging, scans, and Terraform validation.
- Treat Odoo offset paging during concurrent source mutation as a documented first-slice limit.
- Scope Salesforce Bulk API 2.0 plus server-filtered SOQL as separate scale work.
- Continue reviewing the daily operator soak in issue #26.

## Review First

- `src/dander/ingestion/enterprise.py`
- `connectors/odoo.example.yaml`
- `docs/odoo.md`
