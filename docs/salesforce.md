# Salesforce CRM

Dander's Salesforce connector is read-only and uses bounded Bulk API 2.0 CSV streaming for four
independently watermarked endpoints:

| Endpoint | Raw relation | Deletion state |
| --- | --- | --- |
| Accounts | `raw.salesforce_accounts` | `IsDeleted` from `queryAll` |
| Contacts | `raw.salesforce_contacts` | `IsDeleted` from `queryAll` |
| Opportunities | `raw.salesforce_opportunities` | `IsDeleted` from `queryAll` |
| Users | `raw.salesforce_users` | `IsActive` from `query` |

Contact Email and Phone are enabled by default and are personal data. Confirm that the destination
project, operators, retention, and downstream access are appropriate before enabling this pipeline.

## Salesforce setup

Salesforce recommends External Client Apps for new integrations. Configure one for non-interactive
JWT bearer authentication:

1. Generate a 2048-bit RSA private key and self-signed public certificate outside the repository.
2. In **Setup → External Client App Manager**, create a local app and enable OAuth.
3. Add **Manage user data via APIs (`api`)** and **Perform requests at any time
   (`refresh_token`, `offline_access`)**, enable **JWT Bearer Flow**, and upload only the public
   certificate. Dander does not store or use a refresh token.
4. Set permitted users to **Admin approved users are pre-authorized**. Assign a narrow permission
   set with API access and read access to Account, Contact, Opportunity, User, and every selected
   field.
5. Store the consumer key and RSA private key in Dander's configured secret store. Never place a
   credential value in connector YAML.

Newly generated projects include a source-free copy of the complete example. Copy it into the
project root, then edit the connector:

```bash
cp examples/salesforce/dander.yaml dander.yaml
cp examples/salesforce/connectors/salesforce.yaml connectors/salesforce.yaml
cp -R examples/salesforce/models/staging/. models/staging/
mkdir -p models/marts
cp -R examples/salesforce/models/marts/. models/marts/
```

Those commands intentionally turn a newly generated starter into a Salesforce-only project. In an
existing project, copy only the connector/models and merge the `plugins.salesforce` and
`pipelines.salesforce_crm` blocks instead of replacing `dander.yaml`.

Replace `YOUR_DOMAIN`, the API version when necessary, and the JWT subject. Production orgs usually
use `https://login.salesforce.com` for the token URL and audience; sandboxes use
`https://test.salesforce.com`. A My Domain host is supported when it matches the org's OAuth setup.

Custom fields are opt-in. Add each field to both the endpoint SOQL `SELECT` and `raw_schema`, then
add its governed projection to the relevant model. Undeclared fields fail before publication.

## Project configuration

With `dander-connector-salesforce 0.3.1`, a Dander `0.7` project can use:

```yaml
version: 1
plugins:
  salesforce:
    distribution: dander-connector-salesforce
    version: 0.3.1
pipelines:
  salesforce_crm:
    source: salesforce
    models:
      - stg_salesforce__users
      - stg_salesforce__accounts
      - stg_salesforce__contacts
      - stg_salesforce__opportunities
      - fct_salesforce__opportunities
    publish_dataplex: true
    schedule: "0 7 * * *"
    time_zone: America/New_York
    paused: true
    secrets:
      SALESFORCE_EXTERNAL_CLIENT_APP_ID: salesforce-client-id
      SALESFORCE_EXTERNAL_CLIENT_APP_PRIVATE_KEY: salesforce-private-key
```

Existing deployments may keep their current pipeline ID and resource overrides to avoid replacing
Cloud Run and Scheduler resources. The repository's retained project therefore keeps the historical
`salesforce_accounts` ID even though it now runs the complete CRM slice.

Install the exact plugin pin and validate without resolving secrets or contacting Salesforce:

```bash
dander plugins install
dander validate
dander run salesforce_crm --dry-run --project YOUR_GCP_PROJECT
```

## Governed models

The project includes four staging models plus `marts.fct_salesforce__opportunities`. The fact:

- excludes deleted Opportunities and Opportunities tied to deleted Accounts;
- joins Account name, type, and industry;
- joins owner name, alias, type, and active status without filtering inactive owners;
- exposes amount, probability, stage, forecast category, close date, and closed/won flags; and
- publishes descriptions, types, tests, relationships, and metrics through Dander's metadata spine.

Relationship tests ignore nullable keys. Build Users before Accounts, then Contacts and
Opportunities, so owner and Account assertions have an available parent relation.

## Runtime and replay

Each endpoint creates one asynchronous query job, polls with a fixed upper bound, and reads each CSV
page through Salesforce's opaque `Sforce-Locator`. Pages are streamed record by record rather than
materializing an endpoint in memory. Query jobs are deleted after success and handled failure.

Hosted runs apply `SystemModstamp >= <watermark>` independently to each endpoint. The inclusive
boundary deliberately rereads tied timestamps; Dander's SCD1 writer makes replay duplicate-free and
commits a watermark only after the complete endpoint succeeds. Sandbox runs remain full reads.

Soft-deleted Accounts, Contacts, and Opportunities remain in raw/staging with `is_deleted=true`.
The fact filters those tombstones. User deactivation changes `is_active` but retains the owner for
historical reporting. Records already hard-deleted or purged from Salesforce cannot be discovered.

The base SOQL must remain one unfiltered, unordered `SELECT`: Dander owns the watermark predicate,
and omitting `ORDER BY` preserves Salesforce's large-query behavior. A job that exceeds Dander's
bounded polling window fails clearly and can be safely rerun.

Official Salesforce references:

- [External Client Apps](https://developer.salesforce.com/docs/platform/mobile-sdk/guide/eca-create.html)
- [OAuth JWT bearer flow](https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_jwt_flow.htm&type=5)
- [Bulk API 2.0 query guide](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_asynch.pdf)
- [Choosing Bulk API 2.0 for large data sets](https://developer.salesforce.com/blogs/2024/04/accessing-object-data-with-salesforce-platform-apis)
