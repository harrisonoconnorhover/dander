# Experimental Snowflake warehouse

Snowflake is an experimental warehouse adapter, not a supported Dander profile. The current slice
proves native database/schema coordinates, bounded direct and bulk paths, all five writer modes,
an explicit JSON-to-`VARIANT` fallback, fenced table/incremental models, and fenced replace-mode
graph targets. Live qualification and the remaining first-class gates are still required before
support promotion.

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
      # Keep both at zero until a live benchmark establishes the account's crossover.
      direct_max_rows: 0
      direct_max_logical_bytes: 0
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
- The writer sees one complete endpoint stream and retains at most a configured direct threshold
  plus one row in memory. Larger streams continue into bounded Parquet parts without rereading the
  source.
- When both direct thresholds are positive and the complete stream fits, rows are inserted into a
  session-temporary staging table through bounded connector parameters. Otherwise checksummed,
  compressed Parquet parts are uploaded with `PUT` and loaded with `COPY`.
- `COPY` preserves Parquet logical and binary types explicitly. Direct/COPY selection, load query
  IDs, publication query IDs, rows, bytes, duration, and warehouse name flow into run telemetry.
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
- Canonical JSON remains rejected unless its field explicitly declares:

  ```yaml
  extensions:
    - provider: snowflake
      name: fallback
      value: variant
  ```

  The staging value remains canonical JSON text and publication applies `PARSE_JSON`, so the
  destination contains structured `VARIANT` data rather than a quoted string. ARRAY and RECORD
  fallbacks remain unsupported.
- Portable or Snowflake-authored table models replace rows through fenced `DELETE`/`INSERT` DML.
- Incremental models collapse duplicate keys deterministically and accept only rows whose declared
  cursor is at least as new as the stored row. Generic model assertions run after publication.
- Compatible provider-neutral graphs render their existing relational AST as Snowflake SQL and
  publish replace-mode targets through the same stable-table fencing primitive. Every selected
  target renders and validates before the first target is claimed, preventing partial publication
  when a later target uses unavailable semantics.
- Transform staging, publication, and generic assertions report bounded operation telemetry with
  query IDs, duration, affected rows, and warehouse name. SQL and provider response payloads are
  not retained.

The Snowflake role needs usage on its database and warehouse plus permission to create and operate
schemas, tables, and temporary stages in the selected namespace. Use a dedicated disposable role
until a live least-privilege profile is qualified.

## Deliberate limits

All five scalar write patterns are reachable through the warehouse writer capability, while the
ordinary hosted source runner deliberately continues to select SCD1. Graphs remain limited to the
existing replace-mode executable subset; field tests and null-on-failure casts whose Snowflake
semantics have not been proven continue to fail before provider mutation. Views remain unavailable
because Snowflake permanent DDL cannot share the destination-fence transaction. Direct thresholds
default to zero because no live crossover has been measured; do not claim a performance benefit
until that qualification is recorded. Query-history enrichment, live concurrency proof,
infrastructure provisioning, and support promotion remain separate work. Use
`catalog.provider: none` for this experimental path.
