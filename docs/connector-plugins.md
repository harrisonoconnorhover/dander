# Build a connector plugin

Dander connector plugins are ordinary Python distributions. A project activates one only by
pinning its exact distribution and version in `dander.yaml`; an unrelated globally installed
package remains inactive. PyPI supplies package storage and installation, while Dander supplies
the runtime contract, authentication strategies, BigQuery publication, and Druff descriptors.

## Start from the scaffold

Use a lowercase identifier that will remain stable:

```console
dander plugins scaffold acme_crm --display-name "Acme CRM"
cd dander-connector-acme-crm
uv sync --extra dev
uv run pytest
```

The generated project passes its structural tests before it contacts any provider. Its API URL,
record shape, descriptor, and support claims are deliberate placeholders. Replace them before
publishing.

The important generated files are:

- `plugin.py`: API-v1 identity, engine, source factory, and presentation-safe Druff descriptor.
- `source.py`: the provider adapter. It begins as a thin `DltRestSource` subclass.
- `templates/*.example.yaml`: read-only endpoint, pagination, primary key, and declared raw schema.
- `tests/test_plugin.py`: reusable contract, entry-point, and source-factory conformance checks.
- `.github/workflows/`: Linux CI and an inert, manually dispatched trusted-publishing workflow.

The scaffold never creates a GitHub repository, PyPI project, credential, or trusted publisher.

## Choose the smallest source implementation

Keep the generated `DltRestSource` subclass when connector YAML can express the provider's HTTP
requests, selector, pagination, rate limits, and retry behavior. The ServiceNow plugin demonstrates
this pattern: the plugin owns the provider contract while Dander retains the generic bounded REST
transport.

Write a provider-specific `Source` only when the protocol genuinely needs it. The Salesforce
plugin does this for asynchronous Bulk API 2.0 jobs, bounded polling, streamed CSV pages, opaque
locators, and cleanup. Do not duplicate authentication, writers, leases, cursors, or catalog code
inside a plugin.

## Keep authentication in Dander

Connector YAML selects a core strategy such as `api_key_bearer`, `oauth2_client_credentials`,
`oauth2_jwt`, or `oauth1_tba` and names secret references. A plugin receives an `AuthStrategy`; it
must not introduce literal credentials, read arbitrary environment variables, or return secret
settings in its descriptor. Add a new core strategy only for a real authentication model that the
existing strategies cannot represent.

## Meet API v1

The entry point group is `dander.connectors`. Its name, `ConnectorPlugin.plugin_id`, manifest key,
and connector identifier should match. The factory returns `ConnectorPlugin` with API version `1`,
one stable engine key, a callable source factory, and non-secret display metadata.

Use Dander's test kit:

```python
from dander.plugins.testing import assert_plugin_conforms, assert_plugin_distribution

plugin = assert_plugin_conforms(
    create_plugin,
    plugin_id="acme_crm",
    source_config=config,
    auth=auth,
)
assert_plugin_distribution("dander-connector-acme-crm", plugin_id="acme_crm")
```

`source_config` and `auth` are both optional, but they must be supplied together. With both, the
kit verifies the factory returns a real Dander `Source`; without them, it checks only the API-v1
declaration. Provider behavior still needs focused tests.

## Add optional read capabilities

API v1 requires only `discover()` and `extract()`. A concrete source may additionally implement
`get_single_object()`, `count()`, or `test_connection()` using the public protocols exported from
`dander.ingestion`. Dander detects those methods on the source returned by the existing factory;
the `ConnectorPlugin` declaration and API version do not change.

Do not add placeholder methods that raise `NotImplementedError`: method presence advertises real
support. Callers use `SourceCapabilities` (or `ConnectorPluginRegistry.build_capabilities()`) so
an unsupported operation and an invalid plugin result fail with a connector-facing error rather
than an `AttributeError`. The initial capability contract is intentionally read-only; deleted-row
feeds and provider create/update/delete operations require separate runtime semantics.

Inspect the configured source without contacting its provider, then run its optional connection
probe when one is implemented:

```console
dander connector inspect acme_crm
dander connector check acme_crm
```

Both commands also accept a pipeline name and resolve its configured source. `check` uses the same
core authentication and secret-reference path as `dander run`; a successful implementation returns
only a scalar status and no business records.

## Prove provider behavior

Use realistic synthetic fixtures and a stateful loopback simulator for the operations Dander
actually calls. At minimum cover bounded pagination, replay, authentication failure, permission
failure, throttling, malformed records, and provider-specific cleanup. Never weaken declared raw
schema validation to accept bad fixtures.

Then perform one narrow real-account acceptance when access exists. Describe evidence honestly:

- `simulator-validated`: the contract and failures pass locally, but no real tenant was used.
- `provider-validated`: the published candidate completed a bounded real-account proof.
- `supported`: the project additionally commits to the documented support policy.

## Publish and activate

1. Replace every placeholder, choose fixtures containing no private data, and run lint, typing,
   tests, build, outside-checkout installation, and a secret scan.
2. Create the repository and PyPI project yourself. Configure a PyPI trusted publisher for the
   generated GitHub environment named `pypi`.
3. Protect `main`, merge reviewed code, create a tag exactly matching `v<package-version>`, and
   manually run the publish workflow.
4. Pin the published version in the Dander project:

   ```yaml
   plugins:
     acme_crm:
       distribution: dander-connector-acme-crm
       version: 0.1.0
   ```

5. Run `dander plugins install`, `dander validate`, build the source-free image, and use the normal
   reviewed Terraform plan/apply path.

Publishing a package does not make it first-party or supported. Curated discovery records those
claims separately from PyPI metadata.

## Discover curated connectors

Search the small first-party catalog without contacting PyPI at runtime:

```console
dander plugins search
dander plugins search salesforce
```

Each result includes an exact public package pin, its compatible Dander range, support status,
provider-validation status, and documentation links. The catalog ships with Dander; PyPI remains
the package store and source of the actual distribution.

When `dander graph serve` opens a project, `GET /v1/plugin-catalog` exposes the same non-secret
catalog and marks only validated, manifest-declared plugins as installed. `GET /v1/connectors`
continues to describe the runtime connectors that are actually active. Authoring tools may present
catalog setup instructions, but package installation and manifest changes remain explicit operator
actions.
