# NetSuite SuiteQL simulator

## Validation status

This connector is **simulator-validated, not NetSuite-validated**. It is a candidate for a future
`0.3.0` capability after one narrow real-tenant acceptance; it is not part of Dander's public
`0.2.0` support surface.

The implementation follows Oracle's documented SuiteTalk REST Query Service rather than the
record-collection API. Oracle documents that record collections return only IDs and HATEOAS links,
whereas SuiteQL returns selected fields. SuiteQL requires `POST /services/rest/query/v1/suiteql`, a
JSON `q` body, `Prefer: transient`, and `limit`/`offset` paging. Oracle also requires unambiguous
sorting for reliable paging, so the customer query ends in `ORDER BY id`.

The tracked contract is
[`contracts/netsuite-suiteql-simulator.openapi.yaml`](../contracts/netsuite-suiteql-simulator.openapi.yaml).
It deliberately contains six operations:

| Boundary | Operation | Purpose |
|---|---|---|
| NetSuite | `executeSuiteQL` | Read one authenticated, offset-paged customer result. |
| Simulator only | `createCustomer` | Add an invented customer for update/replay tests. |
| Simulator only | `updateCustomer` | Advance that customer's data and watermark. |
| Simulator only | `deleteCustomer` | Remove the proof fixture after the test. |
| Simulator only | `setScenario` | Select a named deterministic failure. |
| Simulator only | `resetSimulator` | Restore fixtures, scenario, and counters. |

## Run locally

```bash
uv sync --extra dev
uv run python -m dander.dev.netsuite_simulator
```

The service binds to `127.0.0.1:8768`; its interactive contract is available at
`http://127.0.0.1:8768/docs`. The printed account ID, all records, and all credentials are
synthetic; fixtures are packaged under
`src/dander/dev/fixtures/netsuite/`.

Run the integration proof with:

```bash
uv run pytest tests/integration/test_netsuite_simulator.py
```

The proof verifies an RFC 5849 HMAC-SHA256 signature, three-page extraction, NetSuite-shaped page
metadata and per-item links, stateful create/update/replay, duplicate-free SCD1 behavior, monotonic
watermarks, and these named scenarios:

- `expired_credentials`
- `throttling`
- `missing_permissions`
- `malformed_record`
- `malformed_response`

## Known boundaries

- The first slice fully rereads customers. Its watermark is operational evidence, not a
  server-side incremental filter.
- SuiteQL REST queries return at most 100,000 results. Larger tenants require a different read
  strategy such as SuiteAnalytics Connect.
- Offset paging can observe source changes between pages. Unique ordering prevents ambiguous
  ordering but does not create a source snapshot.
- Tenant roles, enabled features, Records Catalog field availability, custom fields, concurrency
  limits, and production response variations remain unproven.
- The template uses Dander's existing OAuth1 TBA implementation for compatibility testing. Oracle
  recommends OAuth2 and says that, beginning in 2027.1, new TBA integrations can no longer be
  created for REST web services, RESTlets, or SOAP web services. A future supported release must
  validate the current OAuth2 path available to its test tenant.

## Later real-tenant acceptance

Use a customer, consultant, employer, or SuiteCloud Developer Network sandbox with explicit
authorization. Keep the acceptance narrow:

1. Confirm the tenant-specific account hostname, role permissions, Records Catalog fields, and
   supported OAuth2 setup.
2. Query a small set of non-sensitive test customers through two pages.
3. Create or select one authorized synthetic customer, update it once, run Dander again, and replay
   once; confirm no duplicate key and no watermark regression.
4. Remove one required permission and confirm a clear failure, then restore it.
5. Store no tenant credential or response fixture in this repository.

Primary Oracle references:

- [Executing SuiteQL Queries Through REST Web Services](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_157909186990.html)
- [Using SuiteQL](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_158394344595.html)
- [Listing All Record Instances](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1540810951.html)
- [Setting Up Authentication for a REST Web Services Integration](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/article_0627022005.html)
- [The OAuth 1.0 Signature for REST Web Services](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1534941088.html)
