# Odoo JSON-2 connector

Dander's first Odoo slice reads contacts and companies (`res.partner`) through the Odoo 19+
JSON-2 API. It uses bearer API-key authentication, bounded offset pages, declared raw fields,
idempotent SCD1 publication, and an inclusive `write_date` watermark boundary.

## Free development environment

Odoo Online's free and Standard plans do not expose the external API. For development without an
Odoo license fee, run Odoo 19 Community and PostgreSQL locally using the official Docker images.
The official image instructions show the required PostgreSQL service and Odoo port `8069`.

After creating a database and admin user, create an API key under **Preferences → Account
Security → New API Key**. Copy `connectors/odoo.example.yaml` to `connectors/odoo.yaml`, set the
database name, and expose only the key to Dander:

```bash
cp connectors/odoo.example.yaml connectors/odoo.yaml
read -rs ODOO_API_KEY && printf '\n'
export ODOO_API_KEY
uv run dander run odoo --dry-run --sandbox --project YOUR_NO_BILLING_GCP_PROJECT
uv run dander run odoo --sandbox --project YOUR_NO_BILLING_GCP_PROJECT \
  --build-models --select-model stg_odoo__partners
```

For hosted execution, store the key in Secret Manager and map `ODOO_API_KEY` to that secret in the
pipeline manifest. Never commit the key or a populated local connector.

## Current boundary

This is intentionally one read-only model. It does not write to Odoo, discover arbitrary models,
or support Odoo 18's deprecated XML-RPC/JSON-RPC APIs. Offset pages are ordered by immutable Odoo
record ID. The inclusive `write_date` boundary may replay tied rows; Dander's SCD1 writer makes
that safe. Concurrent source mutations can still shift membership between offset pages, so this
first slice is intended for evaluation and modest tables until snapshot/keyset paging is added.
API keys expire and must be rotated according to the Odoo account's policy.

References:

- [Odoo 19 external JSON-2 API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html)
- [Official Odoo Docker image](https://hub.docker.com/_/odoo)
- [Odoo pricing](https://www.odoo.com/pricing)
