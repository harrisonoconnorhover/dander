# Platform profiles and version 2 projects

Dander version 2 separates reusable pipeline intent from environment-specific deployment choices:

- `dander.yaml` contains connector plugins, sources, graphs/models, tests, and catalog intent.
- `dander.platforms.yaml` contains named warehouse, state, catalog, secret, and launcher selections,
  plus runtime limits, schedules, secret references, and provider resource names.

The first supported hosted profile remains BigQuery, BigQuery state, Dataplex (or no cloud
catalog), GCP Secret Manager, and Cloud Run. A provider name outside the installed and
supported contract fails validation; configuration does not imply provider support.

One logical project may have multiple named platforms and deployments. `dander validate` accepts
`--deployment`; the Python configuration loader accepts the same explicit selection. Other current
GCP commands require a single deployment until the shared provider factories are introduced.
When a project has exactly one deployment, Dander selects it deterministically; multiple
deployments never fall back to an arbitrary default.

## Migrate a version 1 project

Version 1 combined manifests remain supported during the compatibility window. First run the
read-only check:

```bash
dander config migrate --config dander.yaml --check
```

The check renders both files in memory, resolves the generated GCP/Cloud Run deployment, and
requires its platform settings, plugin pins, pipeline behavior, Terraform pipeline projection, and
stable resource names to equal the version 1 result. It changes no file.

Then write the deterministic split:

```bash
dander config migrate --config dander.yaml
dander validate --config dander.yaml --platforms-config dander.platforms.yaml
```

Migration refuses to overwrite an existing `dander.platforms.yaml`. Commit and review both files
together. Existing Terraform addresses do not change merely because the equivalent configuration
is represented in two files.

The current runtime continues to support only the GCP compatibility composition. Provider
registries and additional adapters arrive in later, separate portability changes.
