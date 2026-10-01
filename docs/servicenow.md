# ServiceNow incidents connector

Dander's first ServiceNow slice reads the `incident` table through the official Table API. It is
deliberately read-only: the connector performs one OAuth token operation and one paged GET. The
simulator's create, update, and delete operations exist only to prepare synthetic acceptance data.

For the curated package, pin `dander-connector-servicenow==0.2.3` through the project's manifest:

```yaml
plugins:
  servicenow:
    distribution: dander-connector-servicenow
    version: 0.2.3
```

This package accepts Dander `>=0.5.0,<0.10`, including the public RC20 beta and RC34 integration
candidate. Run `dander plugins install` and `dander validate`, then rebuild the runtime image.
See [connector discovery](connector-plugins.md#discover-curated-connectors) for the distinction
between package compatibility and live provider evidence.

The tracked contract is
[`contracts/servicenow-table-simulator.openapi.yaml`](../contracts/servicenow-table-simulator.openapi.yaml).
Its seven operations are:

| Boundary | Operation | Purpose |
|---|---|---|
| ServiceNow | `issueAccessToken` | Obtain a short-lived client-credentials bearer token. |
| ServiceNow | `listIncidents` | Read an ordered, offset-paged incident collection. |
| Acceptance setup | `createIncident` | Create a synthetic proof incident. |
| Acceptance setup | `updateIncident` | Advance that proof incident. |
| Acceptance setup | `deleteIncident` | Remove the proof object; no delete propagation is claimed. |
| Simulator only | `setScenario` | Select one named deterministic failure. |
| Simulator only | `resetSimulator` | Restore fixtures and normal behavior. |

## Run the simulator

```bash
uv sync --extra dev
uv run python -m dander.dev.servicenow_simulator
uv run pytest tests/integration/test_servicenow_simulator.py
```

The service binds to `127.0.0.1:8767`; interactive FastAPI documentation is available at
`http://127.0.0.1:8767/docs`. Fixtures and credentials are invented. Named scenarios are
`expired_credentials`, `throttling`, `missing_permissions`, and `malformed_record`.

## Configure a ServiceNow instance

1. Use a non-production instance and an integration identity that can read incidents.
2. Enable inbound OAuth client credentials. On ServiceNow this requires the OAuth 2.0 plugin,
   `glide.oauth.inbound.client.credential.grant_type.enabled=true`, and an OAuth application user
   associated with the client.
3. Create an OAuth API endpoint for external clients and keep its client ID and secret outside
   connector YAML. The token URL is `https://INSTANCE.service-now.com/oauth_token.do`.
4. Copy `connectors/servicenow.example.yaml` to `connectors/servicenow.yaml`, replace `INSTANCE`,
   and make `SERVICENOW_CLIENT_ID` and `SERVICENOW_CLIENT_SECRET` resolvable through the normal
   environment or Secret Manager path.
5. Validate the credential and read contract before provisioning:

```bash
uv run dander run servicenow --dry-run --project YOUR_PROJECT
```

ServiceNow's Table API can return reference fields as `{link, value}` objects and date values as
timezone-less UTC strings. The connector therefore requests only declared fields, forces internal
primitive values with `sysparm_display_value=false` and `sysparm_exclude_reference_link=true`, and
casts date strings in the staging model.

## Correctness boundary

The first slice performs a full ordered read on every run and publishes through Dander's
idempotent SCD1 writer. It does **not** claim incremental extraction. Timestamp-watermark filtering
combined with offset paging can skip records when a busy table changes during traversal. A later
incremental version must use keyset pagination on `(sys_updated_on, sys_id)` rather than bolting a
timestamp filter onto offsets.

Deleting an incident from ServiceNow does not delete its existing BigQuery row in this version.
The live acceptance should create a clearly named proof incident, run ingestion, update and ingest
again, replay once, and then clean up only the source proof object.

Primary ServiceNow references:

- [REST APIs](https://www.servicenow.com/docs/r/api-reference/rest-api-explorer/c_RESTAPI.html)
- [Enable OAuth with inbound REST](https://www.servicenow.com/docs/r/api-reference/rest-api-explorer/t_EnableOAuthWithREST.html)
- [Client credentials grant workflow](https://www.servicenow.com/docs/r/platform-security/authentication/client-credentials-grant-workflow.html)
