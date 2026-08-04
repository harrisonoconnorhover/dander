# Salesforce Accounts

Dander's Salesforce slice is intentionally read-only: one Accounts Bulk API 2.0 `queryAll` job,
server-filtered SOQL, streaming locator pagination, a declared raw schema, SCD1 publication, one
staging model, and the existing transform/test/run-history path. It does not modify Salesforce
business records.

Salesforce restricts creation of legacy Connected Apps as of Spring '26 and recommends External
Client Apps for new integrations. Configure one External Client App for non-interactive JWT bearer
authentication:

1. Generate a 2048-bit RSA private key and self-signed public certificate outside the repository.
2. In **Setup → External Client App Manager**, create a local app and enable OAuth.
3. Add **Manage user data via APIs (`api`)** and **Perform requests at any time
   (`refresh_token`, `offline_access`)**, enable **JWT Bearer Flow**, and upload only the public
   certificate. Salesforce requires the refresh-token scope when an External Client App uses this
   preauthorized JWT flow; Dander does not store or use a refresh token.
4. Set permitted users to **Admin approved users are pre-authorized**, select a narrow permission
   set that can read Account and the selected fields, and assign it to the JWT subject user.
5. Copy the consumer key. Store it and the private key as secret values; never place either value
   in connector YAML.

Copy and edit the template:

```bash
cp connectors/salesforce_jwt.example.yaml connectors/salesforce.yaml
```

Replace `YOUR_DOMAIN`, the API version when necessary, and the JWT `subject`. Production orgs use
`https://login.salesforce.com` as both authorization-server audience and token host; sandboxes use
`https://test.salesforce.com`. My Domain hosts are also supported when both values follow the org's
OAuth configuration.

First validate the connector shape without resolving secrets or contacting Salesforce:

```bash
uv run dander run salesforce --dry-run --sandbox --project YOUR_NO_BILLING_GCP_PROJECT
```

That command is configuration-only; it does not prove authentication. For a real local extraction,
authenticate Application Default Credentials to a BigQuery Sandbox GCP project with billing
disabled, then resolve the template's two references from environment variables and omit
`--dry-run`:

```bash
gcloud auth application-default login
export SALESFORCE_EXTERNAL_CLIENT_APP_ID='the-consumer-key'
export SALESFORCE_EXTERNAL_CLIENT_APP_PRIVATE_KEY="$(< /secure/path/dander-salesforce.key)"
uv run dander run salesforce --sandbox --project YOUR_NO_BILLING_GCP_PROJECT
```

The caller must be able to read the project's billing status and create/write the BigQuery Sandbox
dataset. The real command authenticates to Salesforce, streams Accounts through bounded CSV
result pages, replaces the raw sandbox table, and records local run/cursor state in
`.dander/state.db`. Sandbox runs intentionally perform a complete read; hosted runs inject the
committed `SystemModstamp` into the next SOQL query.

A hosted pipeline should map those same environment names to two Secret Manager containers in
`dander.yaml`; Terraform manages the containers and least-privilege runtime access, not secret
versions. Review the plan before applying.

## Current boundary

The connector creates one asynchronous Bulk API 2.0 query job, polls it with a fixed upper bound,
and reads each CSV page through Salesforce's opaque `Sforce-Locator`. The response is streamed a
record at a time rather than materialized as one endpoint-sized list. The result job is deleted
after success or handled failure. QueryAll preserves soft-delete visibility, and the inclusive
`SystemModstamp >= <watermark>` boundary makes tied timestamps replay-safe through hosted SCD1.

The base SOQL must remain one unfiltered, unordered `SELECT`: Dander owns the watermark predicate,
and omitting `ORDER BY` preserves Salesforce PK chunking for large jobs. Bulk jobs have no
completion SLA; a job that exceeds Dander's bounded polling window fails clearly and can be rerun.

Official Salesforce references:

- [External Client Apps](https://developer.salesforce.com/docs/platform/mobile-sdk/guide/eca-create.html)
- [OAuth JWT bearer flow](https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_jwt_flow.htm&type=5)
- [Bulk API 2.0 query guide](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_asynch.pdf)
- [Choosing Bulk API 2.0 for large data sets](https://developer.salesforce.com/blogs/2024/04/accessing-object-data-with-salesforce-platform-apis)
