# Morning Handoff

## Finished

- Added an experimental Snowflake provider with native database/schema coordinates.
- Added bounded Parquet `PUT`/`COPY`, scalar SCD1 merge, schema evolution, and telemetry.
- Explicitly committed fence claims and fenced publication/load history in Snowflake transactions.
- Made remote staging session-temporary and rejected oversized parts before upload.
- Kept Snowflake experimental with transforms, graphs, other modes, and live support excluded.

## Try It

Configure a version 2 Snowflake profile from `docs/snowflake.md`, select `build_models: false`, and
use either BigQuery or PostgreSQL state. No live Snowflake proof is claimed.

## Checks

- Full suite passed: 1,061 tests with PostgreSQL 15; Ruff and strict mypy also passed.
- Wheel/sdist inspection, source-free installs, generated-project validation, and runtime-all import passed.
- Container build/conformance, Terraform/AWS tests, Helm validation, and dependency audit passed.
- Retained stage-zero plan: no changes; no cloud apply or mutation occurred.
- Retained platform plan matched clean `origin/main`: 2 adds/5 updates already pending, no deletes.

## Decisions

- Snowflake translates native database/schema values into canonical `RelationRef` coordinates.
- Explicit Parquet logical/binary settings are part of every effective `COPY` file format.
- Scalar SCD1 is the smallest honest experimental slice; other capabilities remain fail-closed.

## Remaining

- Review the known retained-platform baseline drift before any future apply.
- Open the focused draft PR and require protected CI.
- Merge only after review; do not claim live Snowflake qualification.

## Review First

- `src/dander/providers/snowflake/writer.py`
- `src/dander/providers/snowflake/fence.py`
- `src/dander/providers/snowflake/config.py`
