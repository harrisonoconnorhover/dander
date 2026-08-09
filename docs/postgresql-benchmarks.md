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
