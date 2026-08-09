# Experimental Snowflake warehouse

Snowflake is an experimental warehouse adapter, not a supported Dander profile. The current slice
exists to prove the provider boundary with native database/schema coordinates and a real bounded
bulk path before transforms or additional write modes are added.

## Configuration

Select Snowflake in a version 2 platform profile. Configuration stores only credential references:

```yaml
platforms:
  snowflake_test:
    warehouse:
      provider: snowflake
      account: myorg-myaccount
      user: DANDER_USER
      database: DANDER_TEST
      schema: RAW
      warehouse: DANDER_WH
      role: DANDER_ROLE
      auth:
        method: oauth
        token_env: DANDER_SNOWFLAKE_OAUTH_TOKEN
    state:
      provider: postgresql
      authority_id: postgresql:snowflake-test
    catalog:
      provider: none
    secrets:
      provider: environment
```

Key-pair authentication is also available by setting `method: key_pair` and naming the environment
variable that contains the private-key file path. The optional password is another environment
variable reference. Do not put a token, private key, password, or connection string in YAML.

## Current contract

- `database + schema + relation` become one canonical `RelationRef` without GCP aliases.
- Runtime batches are written as checksummed, compressed Parquet parts and uploaded with `PUT`.
- `COPY` preserves Parquet logical and binary types explicitly.
- One session-temporary stage and table contain each batch; normal cleanup is immediate and session
  termination removes them after process death.
- SCD1 publication and load-history recording occur in the same transaction as the exact
  destination fencing-token touch.
- Only declared nullable columns may be added automatically. Extra columns, required additions,
  type drift, nullability drift, malformed rows, and oversized singleton parts fail closed.

The Snowflake role needs usage on its database and warehouse plus permission to create and operate
schemas, tables, and temporary stages in the selected namespace. Use a dedicated disposable role
until a live least-privilege profile is qualified.

## Deliberate limits

Only scalar SCD1 ingestion is implemented. Models, tests, graphs, replace, SCD2, snapshot,
incremental writes, semi-structured fields, live concurrency proof, infrastructure provisioning,
and support promotion remain separate work. Use `build_models: false` and `catalog.provider: none`
for this experimental path.
