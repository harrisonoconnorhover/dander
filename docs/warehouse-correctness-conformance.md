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
uv run python scripts/benchmarks/warehouse_correctness.py run \
  --profile-json /secure/bigquery-profile.json \
  --candidate-commit COMMIT_SHA \
  --approved-cost-ceiling-usd 1.00 \
  --cost-approval-reference REVIEW_REFERENCE \
  --output /secure/bigquery-evidence.json
```

Repeat for PostgreSQL, Snowflake, and Redshift, then compare exactly four records:

```bash
uv run python scripts/benchmarks/warehouse_correctness.py compare \
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

Each run owns a random target scope and cleans it in `finally`: one BigQuery table and its exact
staging-name prefix, one PostgreSQL schema, one Snowflake schema, or one Redshift schema plus one S3
prefix. After all four pass, verify the retained GCP Terraform plan is no-drift and record only the
sanitized evidence and reviewed ceilings.

## Current evidence status

The deterministic harness and credential-free contract tests are implemented. No renewed paid
provider execution is claimed by this document. Phase 5 remains open until all four same-commit
live records compare equal, exact cleanup is verified, retained GCP no-drift passes afterward, and
the resulting sanitized evidence is merged through protected CI.
