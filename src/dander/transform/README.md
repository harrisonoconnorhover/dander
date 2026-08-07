# Transform engine

Dander owns the SQL transform layer described in the project decision log. A project is a directory
of SQL files with same-named YAML sidecars. The sidecar is the metadata spine shared by execution,
generic tests, catalog publication, and the semantic registry.

Every sidecar has an authored SQL contract:

```yaml
dialect: bigquery  # portable | bigquery | snowflake | redshift | postgres
```

Existing files default to exact `bigquery` SQL and are never silently translated. A model that
opts into `portable` is parsed into one restricted sqlglot AST and may render to the four declared
warehouse dialects. Exact provider SQL compiles only for its matching target. Portable models must
use `ref()` for physical relations; provider functions and ambiguous constructs fail validation.
The current subset includes projections, filters, explicit joins, `UNION ALL`, aggregations,
deterministic windows, closed scalar functions, and strict casts. It intentionally requires
explicit null ordering, Unicode NFC literals, canonical identifiers, decimal `(38, 9)`, and
microsecond time/timestamp precision. Provider variants, generic assertion rendering, and runtime
selection arrive in separate portability slices; this contract alone is not a support claim.

## Build contract

1. Discover every `*.sql` file and validate its `*.yml` or `*.yaml` sidecar.
2. Resolve model `ref()` calls and conventional `raw_<table>` source references.
3. Reject unknown references and cycles before submitting a query.
4. Render refs through a restricted Jinja environment and require one read-only query matching the
   sidecar's exact or portable dialect contract.
5. Materialize selected models and their dependencies as views, tables, or incremental merges in
   topological order.
6. Run declared not-null, unique, accepted-values, and relationship assertions.

```bash
uv run dander build --project "$PROJECT_ID" --select stg_greenhouse__jobs
uv run dander test --project "$PROJECT_ID" --select stg_greenhouse__jobs
```

`build` materializes and tests; `test` only evaluates existing relations. Both commands accept
`--guarded-free-tier`. Incremental sidecars must declare `unique_key` and `incremental_cursor`.
Their build creates the target if needed, selects rows at or beyond its maximum cursor,
last-record-wins deduplicates each key at that boundary, and merges explicit columns. Including
the boundary (`>=`) makes a repeated build idempotent and avoids losing tied cursor values.
