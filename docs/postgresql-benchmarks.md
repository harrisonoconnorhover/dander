# PostgreSQL portability benchmarks

The repository includes one reproducible, non-sensitive local harness for PostgreSQL 15+:

```bash
export DANDER_TEST_POSTGRES_DSN='postgresql://...'
uv run python -m scripts.benchmarks.postgresql \
  --rows 100000 \
  --payload-bytes 1024 \
  --batch-rows 1000 \
  --concurrent-pipelines 4 \
  --concurrent-rows 5000
```

It streams generated rows through the real bounded COPY/SCD1 path, runs independent pipelines
through the bounded pool, proves an older target fence cannot publish, and checks that temporary
staging is gone. The JSON report contains no DSN, row value, SQL text, host name, or credential.

## Local smoke evidence

On 2026-08-08, the committed harness ran against the pinned PostgreSQL 15.18 container with 50,000
rows, a 1,024-byte payload, 500-row batches, and four concurrent pipelines:

- 52,400,000 estimated logical input bytes in 2.381146 seconds (20,998.292 rows/second);
- 8,000 concurrent rows in 0.143238 seconds (55,850.971 rows/second);
- stale publication rejected and zero temporary staging relations observed;
- peak process RSS was 161,218,560 bytes; cost was not measured.

This is a regression smoke, not scale qualification. No controlled container memory limit was
applied, the input was not ten times a declared container limit, and the report therefore says
`qualification_status=not_evaluated`.

For qualification, run the process inside an externally enforced container memory limit, supply
that same limit with `--qualification-memory-limit-mib`, use logical input at least ten times the
limit, and require peak RSS at or below 80 percent. Record the image digest, provider/service
shape, region, raw provider IDs, date, and approved cost ceiling beside the JSON report. A supplied
limit documents the test environment; it does not itself impose a memory limit.

## Phase 8 exact-candidate evidence

`scripts/benchmarks/postgresql_phase8.py` runs correctness, bulk, incremental, and transform
classes. It requires
pre-committed objective manifests bound to the immutable release, image digest, workload hash, and
zero-dollar local cost ceiling. The harness refuses a non-COPY PostgreSQL writer, verifies exact
table shape and cursor-monotonic incremental results, removes its disposable schemas, and emits
normalized `io.dander.qualification.report/v1` reports.

On 2026-08-14, RC22 passed inside its 2 CPU/512 MiB source-free container against disposable TLS
PostgreSQL 15.18 at 2 CPU/1 GiB:

- a deterministic SCD1 fixture matched approved normalized SHA-256
  `82886fc4c0bc5cfb248df1196b9d29763cad4fac60cf248a91084a185d78c2ee` before and after replay;
- 500,000 narrow rows at 38,681.727 rows/second and 200,000 wide rows at 9,032.608 rows/second;
- a 3,000-row delta against 300,000 seed rows at 16,483.516 rows/second;
- scan, join, ten-category aggregation, incremental update/insert, and 21 generic assertion
  executions over 100,000 facts and 100 dimensions in 1.508 seconds;
- an exact 301,500-row final target, zero cursor-regression changes, zero staging residue, and
  verified schema cleanup;
- measured local service cost of USD 0.

This does not close PostgreSQL crossover or hosted cost. RC22 has only the COPY-backed writer and
therefore no bounded direct path with which to measure a crossover threshold.
