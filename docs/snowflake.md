# Experimental Snowflake warehouse

Snowflake is an experimental warehouse adapter, not a supported Dander profile. The current slice
proves native database/schema coordinates, a bounded bulk path, all five scalar writer modes, and
fenced table/incremental models. Live qualification and the remaining first-class gates are still
required before support promotion.

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
- SCD1, incremental, snapshot, SCD2, and replace publication share the same bounded staging path.
  Load-history recording and final destination DML occur in the same transaction as the exact
  fencing-token touch.
- SCD1 retains the last staged record per business key. Incremental mode adds a monotonic cursor
  comparison. Snapshot mode appends only previously unseen complete rows. SCD2 closes changed
  current rows and writes `valid_from`, `valid_to`, and `is_current`. Replace performs fenced
  `DELETE`/`INSERT`, including an empty-source replacement.
- Only declared nullable columns may be added automatically. Extra columns, required additions,
  type drift, nullability drift, malformed rows, and oversized singleton parts fail closed.
- Portable or Snowflake-authored table models replace rows through fenced `DELETE`/`INSERT` DML.
- Incremental models collapse duplicate keys deterministically and accept only rows whose declared
  cursor is at least as new as the stored row. Generic model assertions run after publication.

The Snowflake role needs usage on its database and warehouse plus permission to create and operate
schemas, tables, and temporary stages in the selected namespace. Use a dedicated disposable role
until a live least-privilege profile is qualified.

## Deliberate limits

All five scalar write patterns are reachable through the warehouse writer capability, while the
ordinary hosted source runner deliberately continues to select SCD1. PipelineGraph execution has
not yet been wired to Snowflake's writer selection.
Views remain unavailable because Snowflake permanent DDL cannot share the destination-fence
transaction. Semi-structured fields, a measured small-load direct path, full telemetry plumbing,
live concurrency proof, infrastructure provisioning, and support promotion remain separate work.
Use `catalog.provider: none` for this experimental path.
