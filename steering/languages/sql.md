# SQL Conventions

For the Transform module and hand-written model SQL.

## Dialect & style

- Declare `dialect` in every repository-owned model sidecar. Existing models use **BigQuery
  Standard SQL** (never legacy); new cross-warehouse models may opt into `portable`.
- Uppercase keywords (`SELECT`, `FROM`, `LEFT JOIN`, `PARTITION BY`); lowercase identifiers.
- **snake_case** for tables and columns. Datasets: `raw` → `staging` → `marts`.
- Lead with **CTEs**, not nested subqueries. One transformation per CTE, named for what it produces.
  Final `SELECT` at the bottom.
- Trailing commas in `SELECT` lists; one column per line for anything non-trivial.
- Explicit column lists — no `SELECT *` in models or anything persisted.
- Qualify columns when more than one table is in scope.

Portable models additionally use only Dander `ref()` relations, lowercase unquoted logical
identifiers, explicit `NULLS FIRST`/`NULLS LAST`, and deterministic window tie-breakers declared
unique in model tests. Provider-specific functions or semantics belong in an exact provider model,
not in the portable subset.

```sql
WITH source AS (
  SELECT id, updated_at, amount
  FROM `raw.marketo_leads`
),

deduped AS (
  SELECT
    id,
    amount,
    updated_at,
    ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_at DESC) AS rn
  FROM source
)

SELECT id, amount, updated_at
FROM deduped
WHERE rn = 1
```

## Types & modeling

- Prefer explicit typing; **STRING for IDs** even when numeric-looking (the classic inference trap).
- Timestamps as `TIMESTAMP` (UTC); dates as `DATE`. Money as `NUMERIC`/`BIGNUMERIC`, never `FLOAT64`.
- **Partition** large tables (usually by load/event date) and **cluster** on common filter keys.
- Write patterns are explicit and match `02-engineering.md`: SCD1 = staging + `MERGE` on business
  key; SCD2 = `MERGE` that closes the prior row (`valid_to`, `is_current=false`) and inserts a new
  version; snapshot = partitioned append; incremental = watermark-bounded.

## Documentation

- Every model has a **YAML sidecar** (one source of truth that also feeds Dataplex catalog aspects
  and the semantic registry): model description, owner, and per-column descriptions + types.
- Header comment in each model: purpose, grain (one row = ?), refresh cadence, upstream sources.
- Document non-obvious business logic inline; explain *why* a filter/dedup exists.
- Declare generic tests (not-null, unique, accepted-values, relationships) in the model YAML.
