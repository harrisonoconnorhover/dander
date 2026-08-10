# Snowflake live qualification

On 2026-08-10, Dander's experimental Snowflake warehouse adapter passed its bounded live
qualification in a disposable Snowflake trial account. This records runtime evidence; it does not
promote Snowflake to a supported profile.

## Boundary

- The qualification used a temporary service user, role, database, X-Small warehouse, resource
  monitor, and RSA key pair.
- The resource monitor used Snowflake's minimum accepted one-credit quota. Exact account-level
  credit attribution was not available during the run; the monitor did not suspend or abort the
  93-second qualification.
- The private key remained in a mode-`0600` operator directory, was never committed or printed,
  and was deleted after teardown.

## Result

The sanitized `io.dander.benchmark.snowflake/v1` report recorded `passed` in 93.05163 seconds with
connector version `10.27.101`. Direct binding wrote two rows in three operations; Parquet `COPY`
wrote two rows in 22 operations. SCD1, SCD2, snapshot, incremental, and replace all passed. The
provider-neutral graph returned two rows. Replay remained duplicate-free, the cursor remained
monotonic, a stale publication was rejected, and two concurrent claim attempts were exercised.

The run finished with zero staging stages and zero staging tables, and verified removal of its
random qualification schema. Postflight account checks returned zero matching qualification
users, roles, databases, warehouses, and resource monitors.

## Findings

Live execution exposed Snowflake's reserved `CURRENT` keyword in generated destination-fence and
SCD2 SQL. The aliases now use `target_row`, with regression coverage. All support claims remain
experimental; views, provider-managed infrastructure, performance crossover measurement, and
synchronous total-credit attribution remain outside this proof.
