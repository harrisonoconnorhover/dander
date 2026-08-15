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

`scripts/benchmarks/postgresql_phase8.py` runs correctness, bulk, incremental, transform, and
PostgreSQL-specific failure classes. It requires
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
- bounded pool exhaustion, terminated-connection replacement, recovered state operations, and
  warehouse cancellation rollback in 173 ms;
- an exact 301,500-row final target, zero cursor-regression changes, zero staging residue, and
  verified schema cleanup;
- measured local service cost of USD 0.

This does not close PostgreSQL crossover or hosted cost. RC22 has only the COPY-backed writer and
therefore no bounded direct path with which to measure a crossover threshold.

## RC23 local crossover evidence

The post-RC22 Phase 8 slice adds an opt-in direct insert path behind paired row and logical-byte
limits. Both limits default to zero, so existing manifests and the exact RC22 evidence continue to
use COPY. When enabled, the writer sees the complete endpoint, selects direct only if both bounds
hold, and otherwise chains the retained prefix back into COPY without loss or reordering. Direct
and COPY use the same transaction-local staging relation, destination fence, and logical
publication statements; emitted load telemetry records the selected transport.

Private arm64 RC23 at commit `2455fc34d4503863060b7bac873be36319c13e4f` and image index
`sha256:8bd35188dbdb09bb33be7132a7681577249677e4b3c8a0e76ede4a2975733064` ran the pre-approved
local crossover workload against TLS PostgreSQL 15.18. The harness alternated COPY and DIRECT over
five repetitions at 1, 10, 100, 1,000, and 5,000 rows, compared exact canonical row hashes, and
verified selected-transport telemetry and cleanup. Median milliseconds were COPY/DIRECT 7/8, 7/7,
8/10, 23/39, and 82/169 respectively. DIRECT tied COPY at 10 rows.

Completion review found the emitted 1,400-byte recommendation omitted field-name bytes counted by
the writer and would therefore select COPY for the measured 10 rows. The corrected harness derives
1,490 bytes from the writer's exact normalized logical-size function, but RC23 is retained only as
historical rows/transport evidence and its `threshold_recorded` objective is invalid. Defaults
remain zero; the replacement candidate must rerun crossover. See
`docs/evidence/phase8/2026-08-14/phase8-completion-review.json`.
