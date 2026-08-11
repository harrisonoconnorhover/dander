# Four-warehouse correctness conformance

Phase 5 uses one bounded fixture to compare the common canonical scalar semantics of BigQuery,
PostgreSQL, Snowflake, and Redshift. The harness is
`scripts/benchmarks/warehouse_correctness.py`. It is a correctness gate, not a throughput, cost,
scale, soak, pairwise-profile, or support qualification.

## What is equal

Every provider receives the same two SCD1 input waves and one exact replay. The schema covers only
the shared scalar intersection: boolean, signed 64-bit integer, exact `DECIMAL(38,9)`, 64-bit
float, string, binary, date, microsecond time, microsecond UTC timestamp, and microsecond local
timestamp. Rows include duplicate-key last-write-wins, an update, an insert, a nullable value,
Unicode normalization, binary values, and exact temporal values.

Readback is normalized by the canonical schema: decimals use fixed scale, binary is base64,
strings use NFC, zoned timestamps use UTC, local timestamps remain timezone-free, and rows sort by
the canonical key. A provider run fails unless its normalized rows equal the fixture's expected
rows before and after replay.

Physical transports are deliberately not compared. BigQuery uses its load-job SCD1 adapter,
PostgreSQL uses COPY, and the bounded Snowflake and Redshift profiles force their direct paths.
The current Redshift Python driver sends byte parameters as hex text, so the direct path explicitly
decodes binary placeholders with `TO_VARBYTE`; for readback, the harness projects `VARBYTE` as
base64 text and strictly decodes it back to bytes before canonical normalization because the same
driver otherwise treats raw binary as UTF-8 text.
Provider-specific types, JSON fallbacks, staged transports, other write modes, materializations,
and fencing behavior retain their separate adapter/qualification tests.

## Live execution

Create one uncommitted JSON profile per provider. Profiles contain only non-secret coordinates and
credential references already accepted by the provider registry:

```json
{"provider":"bigquery","project":"PROJECT","location":"US","dataset":"raw"}
```

```json
{"provider":"postgresql","database":"DATABASE","dsn_env":"DANDER_POSTGRES_DSN"}
```

Snowflake uses its normal `account`, `user`, `database`, `warehouse`, optional `role`, and `auth`
reference block. Redshift uses its normal deployment, endpoint, database, region, IAM role, and S3
staging coordinates. Never put a DSN, OAuth token, private key, AWS credential, or row in a profile
or evidence file.

Obtain a separately reviewed cost ceiling for each renewed live invocation. The CLI has no default
and refuses to mutate a provider unless both a ceiling and stable approval reference are supplied.
The recorded ceiling is evidence, not a hard provider spending cap.

Run each profile against the same full protected-main commit:

```bash
uv run python -m scripts.benchmarks.warehouse_correctness run \
  --profile-json /secure/bigquery-profile.json \
  --candidate-commit COMMIT_SHA \
  --approved-cost-ceiling-usd 1.00 \
  --cost-approval-reference REVIEW_REFERENCE \
  --output /secure/bigquery-evidence.json
```

Repeat for PostgreSQL, Snowflake, and Redshift, then compare exactly four records:

```bash
uv run python -m scripts.benchmarks.warehouse_correctness compare \
  --evidence /secure/bigquery-evidence.json \
  --evidence /secure/postgresql-evidence.json \
  --evidence /secure/snowflake-evidence.json \
  --evidence /secure/redshift-evidence.json \
  --output /secure/four-warehouse-comparison.json
```

Provider evidence contains only fixture/schema/result SHA-256 hashes, normalized row count,
transport name, replay and cleanup booleans, candidate commit, Dander version, timestamps, and cost
approval metadata. It never contains normalized or source rows. The comparison fails on a missing
or duplicate provider, different candidate, unequal hash/count, failed replay, or unverified
cleanup.

A failed run writes a separate sanitized failure record. It identifies only the bounded execution
stage, exception type names, cleanup attempt/result, candidate, timestamps, and reviewed ceiling.
Provider messages, SQL, coordinates, credentials, and rows remain excluded. Failed evidence is not
accepted by the four-provider comparison.

Each run owns a random target scope and cleans it in `finally`: one BigQuery table and its exact
staging-name prefix, one PostgreSQL schema, one Snowflake schema, or one Redshift schema plus one S3
prefix. After all four pass, verify the retained GCP Terraform plan is no-drift and record only the
sanitized evidence and reviewed ceilings.

## Current evidence status

An authorized run on 2026-08-11 used protected-main commit
`c0f3e2cb671eb6ddf1c34c60bc9e761d220cb9ad`, after the BigQuery binary-load correction in PR #184
and the Redshift binary direct-load/readback correction in PR #185. The reviewed per-attempt
ceilings were BigQuery $1, PostgreSQL $0, Snowflake $2, and Redshift $3, with no unapproved paid
rerun.

All four providers passed with the same fixture, canonical-schema, and normalized-row hashes. The
three normalized rows were equal before and after exact replay, every provider verified owned
cleanup, and the [comparison record](evidence/warehouse-correctness/2026-08-11/comparison.json)
reports `all_rows_equal=true` and `all_cleanup_verified=true`. The directory also contains the four
sanitized provider records and no coordinates, credentials, DSNs, private keys, or row values.

Post-run verification found zero BigQuery owned tables, zero PostgreSQL test containers, zero
Snowflake test users/databases/warehouses/roles/resource monitors, and zero Redshift Terraform or
named AWS proof resources. Fresh retained GCP stage-zero and current-equivalent platform plans both
reported exact `No changes.`; the platform plan contained 113 no-op resources. No retained GCP
apply occurred.

This closes the Phase 5 shared correctness requirement. Provider-specific types, fallbacks,
transports, materializations, scale, cost, soak, pairwise profiles, and support promotion remain
outside this proof and retain their documented conformance or Phase 8 gates.
