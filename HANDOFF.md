# Morning Handoff

## Finished

- Merged Odoo JSON-2 through protected PR #53 and deleted its feature branch.
- Proved merged Odoo source-free against ephemeral official Odoo/PostgreSQL containers; replay stayed at five unique partners.
- Replaced Salesforce's synchronous full read with server-filtered Bulk API 2.0 QueryAll and streamed locator pages.
- Proved Salesforce source-free against the disposable dev org and GCP project, including replay and soft deletion.
- Removed the synthetic Odoo containers/API key and synthetic Salesforce Account after their proofs.

## Try It

```bash
cp connectors/salesforce_jwt.example.yaml connectors/salesforce.yaml
# Edit the org domain, JWT subject, and secret references.
uv run dander run salesforce --dry-run --sandbox --project YOUR_PROJECT
```

## Checks

- Ruff lint/format and strict mypy passed; all 677 tests passed on the final Salesforce code.
- Dependency audit, Terraform validation, distribution inspection, and local container checks passed.
- Odoo live runs extracted 5 then 3 boundary rows while preserving 5 unique rows and clearing the lease.
- Salesforce live runs proved 13-row initialization, tied-boundary replay, one-row filtered replay, and one-row soft-delete capture; 14 IDs remained unique.
- No retained scheduler, retained deployment, version, tag, or public package changed.

## Decisions

- Salesforce uses an inclusive `SystemModstamp >= watermark` boundary so tied timestamps are replayed safely through idempotent SCD1.
- Bulk query jobs omit `ORDER BY` and `LIMIT` to preserve Salesforce's large-query behavior.
- NetSuite remains simulator-validated and is not represented as real-tenant support.

## Remaining

- Push `codex/salesforce-bulk2-scale`, open its focused PR, and merge only after protected CI passes.
- Add sanitized Odoo and Salesforce results to operator-soak issue #26 without changing schedules.
- Prepare a separate version-only `0.3.0rc1` PR from merged `main`; do not tag or publish it.

## Review First

- `src/dander/ingestion/enterprise.py`
- `connectors/salesforce_jwt.example.yaml`
- `docs/salesforce.md`
